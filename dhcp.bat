@echo off
echo Switching Wi-Fi back to Automatic DHCP...
netsh interface ipv4 set address name="Wi-Fi" source=dhcp
netsh interface ipv4 set dnsservers name="Wi-Fi" source=dhcp
echo Done!
pause
