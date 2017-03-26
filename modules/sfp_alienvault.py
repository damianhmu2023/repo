# -*- coding: utf-8 -*-
# -------------------------------------------------------------------------------
# Name:         sfp_alienvault
# Purpose:      Query AlienVault OTX
#
# Author:      Steve Micallef
#
# Created:     26/03/2017
# Copyright:   (c) Steve Micallef
# Licence:     GPL
# -------------------------------------------------------------------------------

import json
import base64
from datetime import datetime
import time
from netaddr import IPNetwork
from sflib import SpiderFoot, SpiderFootPlugin, SpiderFootEvent

class sfp_alienvault(SpiderFootPlugin):
    """AlienVault OTX Exchange:Investigate,Passive:Blacklists:apikey:Obtain information from AlienVault OTX"""

    # Default options
    opts = {
        "apikey": "",
        "age_limit_days": 30,
        "threat_score_min": 2
    }

    # Option descriptions
    optdescs = {
        "apikey": "Your AlienVault OTX API Key",
        "age_limit_days": "Ignore any records older than this many days. 0 = unlimited.",
        "threat_score_min": "Minimum AlienVault threat score."
    }

    # Be sure to completely clear any class variables in setup()
    # or you run the risk of data persisting between scan runs.

    results = dict()
    errorState = False

    def setup(self, sfc, userOpts=dict()):
        self.sf = sfc
        self.results = dict()

        # Clear / reset any other class member variables here
        # or you risk them persisting between threads.

        for opt in userOpts.keys():
            self.opts[opt] = userOpts[opt]

    # What events is this module interested in for input
    def watchedEvents(self):
        return ["IP_ADDRESS", "AFFILIATE_IPADDR", "INTERNET_NAME",
                "CO_HOSTED_SITE", "NETBLOCK_OWNER", "NETBLOCK_MEMBER",
                "AFFILIATE_INTERNET_NAME", "IPV6_ADDRESS"]

    # What events this module produces
    def producedEvents(self):
        return ["MALICIOUS_IPADDR", "MALICIOUS_INTERNET_NAME",
                "MALICIOUS_COHOST", "MALICIOUS_AFFILIATE_INTERNET_NAME",
                "MALICIOUS_AFFILIATE_IPADDR", "MALICIOUS_NETBLOCK",
                "CO_HOSTED_SITE"]

    def query(self, qry, querytype):
        ret = None
        targettype = "hostname"

        if ":" in qry:
            targettype = "IPv6"

        if self.sf.validIP(qry):
            targettype = "IPv4"

        if querytype not in ["passive_dns", "reputation"]:
            querytype = "reputation"

        url = "https://otx.alienvault.com:443/api/v1/indicators/" + targettype + \
              "/" + qry + "/" + querytype
        headers = {
            'Accept': 'application/json',
            'X-OTX-API-KEY': self.opts['apikey']
        }
        res = self.sf.fetchUrl(url, timeout=self.opts['_fetchtimeout'], 
                               useragent="SpiderFoot", headers=headers)

        if res['code'] == "403":
            self.sf.error("AlienVault OTX API key seems to have been rejected or you have exceeded usage limits for the month.", False)
            self.errorState = True
            return None

        if res['content'] is None or res['code'] == "404":
            self.sf.info("No AlienVault OTX info found for " + qry)
            return None

        try:
            info = json.loads(res['content'])
        except Exception as e:
            self.sf.error("Error processing JSON response from AlienVault OTX.", False)
            return None

        return info


    # Handle events sent to this module
    def handleEvent(self, event):
        eventName = event.eventType
        srcModuleName = event.module
        eventData = event.data

        if self.errorState:
            return None

        self.sf.debug("Received event, " + eventName + ", from " + srcModuleName)

        if self.opts['apikey'] == "":
            self.sf.error("You enabled sfp_alienvault but did not set an API key/password!", False)
            self.errorState = True
            return None

        # Don't look up stuff twice
        if eventData in self.results:
            self.sf.debug("Skipping " + eventData + " as already mapped.")
            return None
        else:
            self.results[eventData] = True

        qrylist = list()
        if eventName.startswith("NETBLOCK_"):
            for ipaddr in IPNetwork(eventData):
                qrylist.append(str(ipaddr))
                self.results[str(ipaddr)] = True
        else:
            qrylist.append(eventData)

        # For IP Addresses, do the additional passive DNS lookup
        if eventName == "IP_ADDRESS":
            evtType = "CO_HOSTED_SITE"
            ret = self.query(eventData, "passve_dns")
            if ret is None:
                self.sf.info("No Passive DNS info for " + eventData)
            elif "passve_dns" in ret:
                self.sf.debug("Found passive DNS results in AlienVault OTX")
                res = ret["passive_dns"]
                for rec in res:
                    if "hostname" in rec:
                        last = rec.get("last", "")
                        last_dt = datetime.strptime(last, '%Y-%m-%d %H:%M:%S')
                        last_ts = int(time.mktime(last_dt.timetuple()))
                        age_limit_ts = int(time.time()) - (86400 * self.opts['age_limit_days'])
                        host = rec['hostname']
                        if self.opts['age_limit_days'] > 0 and last_ts < age_limit_ts:
                            self.sf.debug("Record found but too old, skipping.")
                            continue
                        else:
                            e = SpiderFootEvent(evtType, host, self.__name__, event)
                            self.notifyListeners(e)

        for addr in qrylist:
            if self.checkForStop():
                return None

            if eventName == 'IP_ADDRESS' or eventName.startswith('NETBLOCK_'):
                evtType = 'MALICIOUS_IPADDR'
            if eventName == "AFFILIATE_IPADDR":
                evtType = 'MALICIOUS_AFFILIATE_IPADDR'
            if eventName == "INTERNET_NAME":
                evtType = "MALICIOUS_INTERNET_NAME"
            if eventName == 'AFFILIATE_INTERNET_NAME':
                evtType = 'MALICIOUS_AFFILIATE_INTERNET_NAME'
            if eventName == 'CO_HOSTED_SITE':
                evtType = 'MALICIOUS_COHOST'

            rec = self.query(addr, "reputation")
            if rec is not None:
                if rec.get("reputation", None):
                    self.sf.debug("Found reputation info in AlienVault OTX")
                    rec_history = rec['reputation'].get("activities", list())
                    if rec['reputation']['threat_score'] < self.opts['threat_score_min']:
                        continue
                    descr = "Threat Score: " + str(rec['reputation']['threat_score']) + ":"

                    for result in rec_history:
                        descr += "\n - " + result.get("name", "")
                        created = result.get("last_date", "")
                        # 2014-11-06T10:45:00.000
                        created_dt = datetime.strptime(created, '%Y-%m-%dT%H:%M:%S')
                        created_ts = int(time.mktime(created_dt.timetuple()))
                        age_limit_ts = int(time.time()) - (86400 * self.opts['age_limit_days'])
                        if self.opts['age_limit_days'] > 0 and created_ts < age_limit_ts:
                            self.sf.debug("Record found but too old, skipping.")
                            continue
                        cats_description = ""

                    e = SpiderFootEvent(evtType, descr, self.__name__, event)
                    self.notifyListeners(e)

# End of sfp_alienvault class
