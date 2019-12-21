# $Id: install.py 1483 2016-04-25 06:53:19Z mwall $
# installer for seg
# Copyright 2014 Matthew Wall

from setup import ExtensionInstaller

def loader():
    return SEGInstaller()

class SEGInstaller(ExtensionInstaller):
    def __init__(self):
        super(SEGInstaller, self).__init__(
            version="0.9",
            name='seg',
            description='Upload weather data to SmartEnergyGroups.',
            author="Matthew Wall",
            author_email="mwall@users.sourceforge.net",
            restful_services='user.seg.SEG',
            config={
                'StdRESTful' : {
                    'SmartEnergyGroups': {
                        'token': 'INSERT_TOKEN_HERE',
                        'node': 'INSERT_STATION_IDENTIFIER_HERE'}}},
            files=[('bin/user', ['bin/user/seg.py'])]
            )
