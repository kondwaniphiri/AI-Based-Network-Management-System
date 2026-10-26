@echo off
echo Setting Static IP for MiFi...
netsh interface ipv4 set address name="Wi-Fi" source=static address=192.168.8.100 mask=255.255.255.0 gateway=192.168.8.1
netsh interface ipv4 set dnsservers name="Wi-Fi" source=static address=8.8.8.8
echo Done!
pause
