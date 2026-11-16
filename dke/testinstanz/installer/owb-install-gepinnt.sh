#!/bin/bash
# Pinned installer for the openWB test instance (dke/testinstanz, gate 1).
# Derived from openwb-install.sh at cf803dc8; deviations: see dke/testinstanz/README.md.
OPENWBBASEDIR=/var/www/html/openWB
OPENWB_USER=openwb
OPENWB_GROUP=openwb
OPENWB_REPO=https://github.com/dke-coding/openwb-core.git
OPENWB_TAG=deployed-2026-10-28
OPENWB_COMMIT=cf803dc8b8b7a105697e8537b687908a11b0d020

if (( $(id -u) != 0 )); then
	echo "this script has to be run as user root or with sudo"
	exit 1
fi

echo "installing openWB 2 into \"${OPENWBBASEDIR}\""

# proxy: if http_proxy or https_proxy is set, every network access of this script and of
# install_packages.sh uses it (apt via apt.conf.d, sudo via env_keep, curl/wget/git/pip via env)
OWB_PROXY_APT=/etc/apt/apt.conf.d/95owb-testinstanz-proxy
OWB_PROXY_SUDO=/etc/sudoers.d/owb-testinstanz-proxy
if [ -n "${http_proxy:-}${https_proxy:-}" ]; then
	http_proxy="${http_proxy:-$https_proxy}"
	https_proxy="${https_proxy:-$http_proxy}"
	HTTP_PROXY="$http_proxy"
	HTTPS_PROXY="$https_proxy"
	export http_proxy https_proxy HTTP_PROXY HTTPS_PROXY
	if [ -n "${no_proxy:-}" ]; then
		NO_PROXY="$no_proxy"
		export no_proxy NO_PROXY
	fi
	printf 'Acquire::http::Proxy "%s";\nAcquire::https::Proxy "%s";\n' "$http_proxy" "$https_proxy" >"$OWB_PROXY_APT"
	echo "proxy enabled for apt, sudo, curl, wget, git and pip"
fi

# git, sudo and certificates first: install_packages.sh is taken from the pinned clone
apt-get -q update || exit 1
apt-get -q -y install git sudo ca-certificates || exit 1
mkdir -p "$(dirname "$OPENWBBASEDIR")" || exit 1
if [ -n "${http_proxy:-}" ]; then
	echo 'Defaults env_keep += "http_proxy https_proxy no_proxy HTTP_PROXY HTTPS_PROXY NO_PROXY"' >"$OWB_PROXY_SUDO"
	chmod 440 "$OWB_PROXY_SUDO"
	if ! visudo -cf "$OWB_PROXY_SUDO" >/dev/null; then
		rm -f "$OWB_PROXY_SUDO"
		echo "sudoers proxy entry invalid, aborting"
		exit 1
	fi
fi

echo "create group $OPENWB_GROUP"
# Will do nothing if group already exists:
/usr/sbin/groupadd "$OPENWB_GROUP"
echo "done"

echo "create user $OPENWB_USER"
# Will do nothing if user already exists:
/usr/sbin/useradd "$OPENWB_USER" -g "$OPENWB_GROUP" --create-home
echo "done"

# The user "openwb" is still new and we might need sudo in many places. Thus for now we give the user
# unrestricted sudo. This should be restricted in the future
echo "$OPENWB_USER ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/openwb
chmod 440 /etc/sudoers.d/openwb
echo "done"

echo "check for initial git clone..."
if [ ! -d "${OPENWBBASEDIR}/web" ]; then
	mkdir "$OPENWBBASEDIR"
	chown "$OPENWB_USER:$OPENWB_GROUP" "$OPENWBBASEDIR"
	sudo -u "$OPENWB_USER" git clone "$OPENWB_REPO" --branch "$OPENWB_TAG" "$OPENWBBASEDIR"
	echo "git cloned"
else
	echo "ok"
fi

# pinned commit check (as owner of the clone, git safe.directory)
OWB_HEAD=$(sudo -u "$OPENWB_USER" git -C "$OPENWBBASEDIR" rev-parse HEAD)
if [ "$OWB_HEAD" != "$OPENWB_COMMIT" ]; then
	echo "wrong commit in ${OPENWBBASEDIR}: '${OWB_HEAD}', expected ${OPENWB_COMMIT}, aborting"
	exit 1
fi
echo "commit ${OPENWB_COMMIT} ok"

# install packages with the script from the pinned clone
bash "${OPENWBBASEDIR}/runs/install_packages.sh" || exit 1

echo -n "check for ramdisk... "
if grep -Fq "tmpfs ${OPENWBBASEDIR}/ramdisk" /etc/fstab; then
	echo "ok"
else
	mkdir -p "${OPENWBBASEDIR}/ramdisk"
	sudo tee -a "/etc/fstab" <"${OPENWBBASEDIR}/data/config/ramdisk_config.txt" >/dev/null
	mount -a
	echo "created"
fi

echo -n "check for crontab... "
if [ ! -f /etc/cron.d/openwb ]; then
	cp "${OPENWBBASEDIR}/data/config/openwb.cron" /etc/cron.d/openwb
	echo "installed"
else
	echo "ok"
fi

# check for mosquitto configuration
echo "updating mosquitto config file"
systemctl stop mosquitto
sleep 2
SRC="${OPENWBBASEDIR}/data/config/mosquitto/public"
cp -a "${SRC}/mosquitto.conf" "${SRC}/mosquitto.acl" /etc/mosquitto/
sudo chown mosquitto:mosquitto /etc/mosquitto/mosquitto.acl
sudo chmod 700 /etc/mosquitto/mosquitto.acl
# enable default listeners: local bridge, public secure and unsecure mqtt and ws listeners with acl files
cp -a "${SRC}/openwb.conf" "${SRC}/openwb-unsecure-acl.conf" "${SRC}/openwb-default-acl.conf" /etc/mosquitto/conf.d/
sudo cp /etc/ssl/certs/ssl-cert-snakeoil.pem /etc/mosquitto/certs/openwb.pem
sudo cp /etc/ssl/private/ssl-cert-snakeoil.key /etc/mosquitto/certs/openwb.key
sudo chgrp mosquitto /etc/mosquitto/certs/openwb.key
systemctl start mosquitto

#check for mosquitto_local instance
SRC="${OPENWBBASEDIR}/data/config/mosquitto/local"
if [ ! -f /etc/init.d/mosquitto_local ]; then
	echo "setting up mosquitto local instance"
	install -d -m 0755 -o root -g root /etc/mosquitto/conf_local.d/
	install -d -m 0755 -o mosquitto -g root /var/lib/mosquitto_local
	cp "${SRC}/mosquitto_local_init" /etc/init.d/mosquitto_local
	chown root:root /etc/init.d/mosquitto_local
	chmod 755 /etc/init.d/mosquitto_local
	systemctl daemon-reload
	systemctl enable mosquitto_local
else
	systemctl stop mosquitto_local
	sleep 2
fi
cp -a "${SRC}/mosquitto_local.conf" /etc/mosquitto/
cp -a "${SRC}/openwb_local.conf" /etc/mosquitto/conf_local.d/
systemctl start mosquitto_local
echo "mosquitto done"

# apache
echo -n "replacing apache default page..."
cp "${OPENWBBASEDIR}/data/config/apache/000-default.conf" "/etc/apache2/sites-available/"
cp "${OPENWBBASEDIR}/index.html" /var/www/html/index.html
echo "done"
echo -n "fix upload limit..."
if [ -d "/etc/php/7.3/" ]; then
	echo "upload_max_filesize = 300M" > /etc/php/7.3/apache2/conf.d/20-uploadlimit.ini
	echo "post_max_size = 300M" >> /etc/php/7.3/apache2/conf.d/20-uploadlimit.ini
	echo "done (OS Buster)"
elif [ -d "/etc/php/7.4/" ]; then
	echo "upload_max_filesize = 300M" > /etc/php/7.4/apache2/conf.d/20-uploadlimit.ini
	echo "post_max_size = 300M" >> /etc/php/7.4/apache2/conf.d/20-uploadlimit.ini
	echo "done (OS Bullseye)"
fi
echo -n "enabling apache ssl module..."
a2enmod ssl
a2enmod proxy_wstunnel
sudo a2dissite default-ssl
sudo cp "${OPENWBBASEDIR}/data/config/apache/apache-openwb-ssl.conf" /etc/apache2/sites-available/
sudo a2ensite apache-openwb-ssl
echo "done"
echo -n "restarting apache..."
systemctl restart apache2
echo "done"

echo "installing python requirements..."
sudo -u "$OPENWB_USER" pip install -r "${OPENWBBASEDIR}/requirements.txt"

echo "installing openwb2 system service..."
ln -s "${OPENWBBASEDIR}/data/config/openwb2.service" /etc/systemd/system/openwb2.service
systemctl daemon-reload
systemctl enable openwb2

echo "installing openwb2-simpleAPI service..."
ln -s "${OPENWBBASEDIR}/data/config/openwb-simpleAPI.service" /etc/systemd/system/openwb-simpleAPI.service
systemctl daemon-reload
systemctl enable openwb-simpleAPI

# test instance: no remote support; masked so that runs/atreboot.sh cannot install it either
echo "masking openwb2 remote support service..."
systemctl stop openwbRemoteSupport 2>/dev/null
if [ -f /etc/systemd/system/openwbRemoteSupport.service ] && [ ! -L /etc/systemd/system/openwbRemoteSupport.service ]; then
	rm -f /etc/systemd/system/openwbRemoteSupport.service
fi
systemctl mask openwbRemoteSupport
systemctl daemon-reload
if [ "$(systemctl is-enabled openwbRemoteSupport 2>/dev/null)" != "masked" ]; then
	echo "openwbRemoteSupport is not masked, aborting"
	exit 1
fi

echo "installation finished, now starting openwb services..."
systemctl start openwb2

echo "all done"
echo "if you want to use this installation for development, add a password for user 'openwb'"
