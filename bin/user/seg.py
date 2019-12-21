# $Id: seg.py 1483 2016-04-25 06:53:19Z mwall $
# Copyright 2013 Matthew Wall

"""
Smart Energy Groups provides real time energy information, beautiful
visualisations, alerts and notifications, iPhone, iPad and Android widgets,
rock solid performance, and open source hardware to help people save money
and energy in real time.

This is a weewx extension that uploads data to Smart Energy Groups.

SEG defaults to using METRICWX units, but this can be changed at SEG.

Minimal Configuration

[StdRESTful]
    [[SmartEnergyGroups]]
        token = TOKEN
        node = station_name

The SEG api is here:

https://smartenergygroups.com/api
"""

# FIXME: support all schema fields with per-field  names, formats, and units
# FIXME: default to seg conventions _p _t others?

import Queue
import re
import sys
import syslog
import urllib
import urllib2

import weewx
import weewx.restx
import weewx.units
from weeutil.weeutil import to_bool, accumulateLeaves

VERSION = "X"

if weewx.__version__ < "3":
    raise weewx.UnsupportedFeature("weewx 3 is required, found %s" %
                                   weewx.__version__)

def logmsg(level, msg):
    syslog.syslog(level, 'restx: SEG: %s' % msg)

def logdbg(msg):
    logmsg(syslog.LOG_DEBUG, msg)

def loginf(msg):
    logmsg(syslog.LOG_INFO, msg)

def logerr(msg):
    logmsg(syslog.LOG_ERR, msg)

def _obfuscate(s):
    return ('X'*(len(s)-4) + s[-4:])

def _compat(d, old_label, new_label):
    if old_label in d and not new_label in d:
        d.setdefault(new_label, d[old_label])
        d.pop(old_label)

# some unit labels are rather lengthy.  this reduces them to something shorter.
UNIT_REDUCTIONS = {
    'degree_F': 'F',
    'degree_C': 'C',
    'inch': 'in',
    'mile_per_hour': 'mph',
    'mile_per_hour2': 'mph',
    'km_per_hour': 'kph',
    'km_per_hour2': 'kph',
    'meter_per_second': 'mps',
    'meter_per_second2': 'mps',
    'degree_compass': None,
    'watt_per_meter_squared': 'Wpm2',
    'uv_index': None,
    'percent': None,
    'unix_epoch': None,
    }

# return the units label for an observation
def _get_units_label(obs, unit_system):
    (unit_type, _) = weewx.units.getStandardUnitType(unit_system, obs)
    return UNIT_REDUCTIONS.get(unit_type, unit_type)

PREFIXES = {
    'degree_F': 'temperature_',
    'degree_C': 'temperature_',
    'watt': 'p_',
    'watt_hour': 'e_',
    'volt': 'v_',
    'amp': 'a_',
    }

# figure out the seg prefix based on unit type
def _get_prefix(obs, unit_system):
    (unit_type, _) = weewx.units.getStandardUnitType(unit_system, obs)
    return PREFIXES.get(unit_type)

# get the template for an observation based on the observation key
def _get_template(obs_key, overrides, append_units_label, unit_system,
                  prepend_seg_label):
    tmpl_dict = dict()
    if append_units_label:
        label = _get_units_label(obs_key, unit_system)
        if label is not None:
            tmpl_dict['name'] = "%s_%s" % (obs_key, label)
    if prepend_seg_label:
        prefix = _get_prefix(obs_key, unit_system)
        if prefix is not None:
            name = tmpl_dict.get('name', obs_key)
            tmpl_dict['name'] = "%s%s" % (prefix, name)
    for x in ['name', 'format', 'units']:
        if x in overrides:
            tmpl_dict[x] = overrides[x]
    return tmpl_dict


class SEG(weewx.restx.StdRESTbase):
    def __init__(self, engine, config_dict):
        """This service recognizes standard restful options plus the following:

        token: unique token
        
        node: station identifier - used as the SEG node

        unit_system: one of US, METRIC, or METRICWX
        Default is METRICWX (which matches the SEG default)

        prepend_seg_label: Indicates whether the SEG prefixes such as p_, e_,
        or temperature_ be prepended to the variable names.  Using the prefixes
        enables automatic device discover.
        Default is True

        append_units_label: Indicates whether units label be appended to name
        Default is True

        obs_to_upload: Which observations to upload.  Possible values are
        none or all.  When none is specified, only items in the streams list
        will be uploaded.  When all is specified, all observations will be
        uploaded, subject to overrides in the streams list.
        Default is all

        streams: dictionary of weewx observation names with optional upload
        name, format, and units
        Default is None
        """
        super(SEG, self).__init__(engine, config_dict)
        loginf("service version is %s" % VERSION)
        try:
            site_dict = config_dict['StdRESTful']['SmartEnergyGroups']
            site_dict = accumulateLeaves(site_dict, max_level=1)
            site_dict['token']
            if 'station' not in site_dict:
                site_dict['node']
        except KeyError, e:
            logerr("Data will not be posted: Missing option %s" % e)
            return

        # for backward compatibility: 'station' is now 'node'
        _compat(site_dict, 'station', 'node')

        site_dict.setdefault('prepend_seg_label', True)
        site_dict.setdefault('append_units_label', True)
        site_dict.setdefault('augment_record', True)
        site_dict.setdefault('obs_to_upload', 'all')
        site_dict['prepend_seg_label'] = to_bool(site_dict.get('prepend_seg_label'))
        site_dict['append_units_label'] = to_bool(site_dict.get('append_units_label'))
        site_dict['augment_record'] = to_bool(site_dict.get('augment_record'))
        # SEG defaults to METRICWX
        usn = site_dict.get('unit_system', 'METRICWX')
        if usn is not None:
            site_dict['unit_system'] = weewx.units.unit_constants[usn]

        if 'streams' in config_dict['StdRESTful']['SmartEnergyGroups']:
            site_dict['streams'] = dict(config_dict['StdRESTful']['SmartEnergyGroups']['streams'])

        # if we are supposed to augment the record with data from weather
        # tables, then get the manager dict to do it.  there may be no weather
        # tables, so be prepared to fail.
        try:
            if site_dict.get('augment_record'):
                _manager_dict = weewx.manager.get_manager_dict_from_config(
                    config_dict, 'wx_binding')
                site_dict['manager_dict'] = _manager_dict
        except weewx.UnknownBinding:
            pass

        self.archive_queue = Queue.Queue()
        self.archive_thread = SEGThread(self.archive_queue, **site_dict)
        self.archive_thread.start()
        self.bind(weewx.NEW_ARCHIVE_RECORD, self.new_archive_record)

        if usn is not None:
            loginf("desired unit system is %s" % usn)
        loginf("Data will be uploaded for node=%s token=%s" %
               (site_dict['node'], _obfuscate(site_dict['token'])))

    def new_archive_record(self, event):
        self.archive_queue.put(event.record)

class SEGThread(weewx.restx.RESTThread):

    _SERVER_URL = 'http://api.smartenergygroups.com/api_sites/stream'

    def __init__(self, queue, token, node, unit_system=None,
                 streams={}, obs_to_upload='all', append_units_label=True,
                 prepend_seg_label=True, augment_record=True,
                 server_url=_SERVER_URL, skip_upload=False,
                 manager_dict=None,
                 log_success=True, log_failure=True,
                 post_interval=300, max_backlog=sys.maxint, stale=None,
                 timeout=60, max_tries=3, retry_wait=5):
        super(SEGThread, self).__init__(queue,
                                        protocol_name='SEG',
                                        manager_dict=manager_dict,
                                        post_interval=post_interval,
                                        max_backlog=max_backlog,
                                        stale=stale,
                                        log_success=log_success,
                                        log_failure=log_failure,
                                        max_tries=max_tries,
                                        timeout=timeout,
                                        retry_wait=retry_wait)
        self.token = token
        self.node = node
        self.upload_all = True if obs_to_upload.lower() == 'all' else False
        self.append_units_label = append_units_label
        self.prepend_seg_label = prepend_seg_label
        self.streams = streams
        self.server_url = server_url
        self.skip_upload = to_bool(skip_upload)
        self.unit_system = unit_system
        self.augment_record = augment_record
        self.templates = dict()

    def process_record(self, record, dbm):
        if self.augment_record and dbm:
            record = self.get_record(record, dbm)
        if self.unit_system is not None:
            record = weewx.units.to_std_system(record, self.unit_system)
        data = self.get_data(record)
        if self.skip_upload:
            loginf("skipping upload")
            return
        req = urllib2.Request(self.server_url, data)
        req.add_header("User-Agent", "weewx/%s" % weewx.__version__)
        req.get_method = lambda: 'PUT'
        self.post_with_retries(req)

    def check_response(self, response):
        txt = response.read()
        if txt.find('(status ok') < 0:
            raise weewx.restx.FailedPost("Server returned '%s'" % txt)

    def get_data(self, record):
        # if uploading everything, we must check the upload variables list
        # every time since variables may come and go in a record.  use the
        # streams to override any generic template generation.
        if self.upload_all:
            for f in record:
                if f not in self.templates:
                    self.templates[f] = _get_template(f,
                                                      self.streams.get(f, {}),
                                                      self.append_units_label,
                                                      record['usUnits'],
                                                      self.prepend_seg_label)

        # otherwise, create the list of upload variables once, based on the
        # user-specified list of streams.
        elif not self.templates:
            for f in self.streams:
                self.templates[f] = _get_template(f, self.streams[f],
                                                  self.append_units_label,
                                                  record['usUnits'],
                                                  self.prepend_seg_label)

        # loop through the templates, populating them with data from the
        # record.  append each to an array that we use to build the url.
        elements = []
        for k in self.templates:
            v = record.get(k)
            if v is not None:
                name = self.templates[k].get('name', k)
                fmt = self.templates[k].get('format', '%s')
                to_units = self.templates[k].get('units')
                if to_units is not None:
                    (from_unit, from_group) = weewx.units.getStandardUnitType(
                        record['usUnits'], k)
                    from_t = (v, from_unit, from_group)
                    v = weewx.units.convert(from_t, to_units)[0]
                vstr = fmt % v
                elements.append('(%s %s)' % (name, vstr))
        if len(elements) == 0:
            return None

        # now build the url
        node = urllib.quote_plus(self.node)
        elements.insert(0, '(node %s %s ' % (node, int(record['dateTime'])))
        elements.append(')')
        elements.insert(0, 'data_post=(site %s ' % self.token)
        elements.append(')')
        data = ''.join(elements)
        if weewx.debug >= 2:
            logdbg('data: %s' % re.sub(r"site [^ ]*", "site XXX", data))
        return data

# for backward compatibility
SmartEnergyGroups = SEG
