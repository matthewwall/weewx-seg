seg - weewx extension that sends data to SmartEnergyGroups
Copyright 2014 Matthew Wall

Installation instructions:

1) run the installer:

wee_extension --install weewx-seg.tgz

2) modify weewx.conf:

[StdRESTful]
    [[SmartEnergyGroups]]
        token = TOKEN
        node = station_name

3) restart weewx

sudo /etc/init.d/weewx stop
sudo /etc/init.d/weewx start
