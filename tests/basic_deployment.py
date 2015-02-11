#!/usr/bin/python

import amulet
import time
import urllib2

from charmhelpers.contrib.openstack.amulet.deployment import (
    OpenStackAmuletDeployment
)

from charmhelpers.contrib.openstack.amulet.utils import (
    OpenStackAmuletUtils,
    DEBUG, # flake8: noqa
    ERROR
)

# Use DEBUG to turn on debug logging
u = OpenStackAmuletUtils(ERROR)


class OpenstackDashboardBasicDeployment(OpenStackAmuletDeployment):
    """Amulet tests on a basic quantum-gateway deployment."""

    def __init__(self, series, openstack=None, source=None, stable=False):
        """Deploy the entire test environment."""
        super(OpenstackDashboardBasicDeployment, self).__init__(series, openstack,
                                                            source, stable)
        self._add_services()
        self._add_relations()
        self._configure_services()
        self._deploy()
        self._initialize_tests()

    def _add_services(self):
        """Add services

           Add the services that we're testing, where quantum-gateway is local,
           and the rest of the service are from lp branches that are
           compatible with the local charm (e.g. stable or next).
           """
        this_service = {'name': 'openstack-dashboard'}
        other_services = [{'name': 'keystone'}]
        super(OpenstackDashboardBasicDeployment, self)._add_services(this_service,
                                                                 other_services)

    def _add_relations(self):
        """Add all of the relations for the services."""
        relations = {
          'openstack-dashboard:identity-service': 'keystone:identity-service',
        }
        super(OpenstackDashboardBasicDeployment, self)._add_relations(relations)

    def _configure_services(self):
        """Configure all of the services."""
        keystone_config = {'admin-password': 'openstack',
                           'admin-token': 'ubuntutesting'}
        configs = {'keystone': keystone_config}
        super(OpenstackDashboardBasicDeployment, self)._configure_services(configs)

    def _initialize_tests(self):
        """Perform final initialization before tests get run."""
        # Access the sentries for inspecting service units
        self.keystone_sentry = self.d.sentry.unit['keystone/0']
        self.openstack_dashboard_sentry = self.d.sentry.unit['openstack-dashboard/0']

        # Let things settle a bit before moving forward
        time.sleep(30)

    def test_services(self):
        """Verify the expected services are running on the corresponding
           service units."""
        dashboard_services = ['service apache2 status']

        commands = {
            self.keystone_sentry: ['status keystone'],
            self.openstack_dashboard_sentry: dashboard_services
        }

        ret = u.validate_services(commands)
        if ret:
            amulet.raise_status(amulet.FAIL, msg=ret)

    def test_local_settings(self):
        unit = self.openstack_dashboard_sentry
        ksentry = self.keystone_sentry
        conf = '/etc/openstack-dashboard/local_settings.py'
        file_contents = unit.file_contents(conf)
        rdata = ksentry.relation('identity-service', 'openstack-dashboard:identity-service')
        expected = {
            'LOGIN_REDIRECT_URL': """'/horizon'""",
            'OPENSTACK_HOST': '"%s"' % (rdata['private-address']),
            'OPENSTACK_KEYSTONE_DEFAULT_ROLE': '"Member"'
        }
        for line in file_contents.split('\n'):
            if '=' in line:
                 args = line.split('=')
                 if len(args) <= 1:
                     continue
                 key = args[0].strip()
                 value = args[1].strip()
                 if key in expected.keys():
                     if expected[key] != value:
                         msg="Mismatch %s != %s" % (expected[key], value)
                         amulet.raise_status(amulet.FAIL, msg=msg)

    def test_connection(self):
        unit = self.openstack_dashboard_sentry
        dashboard_relation = unit.relation('identity-service',
                                           'keystone:identity-service')
        dashboard_ip = dashboard_relation['private-address']
        response = urllib2.urlopen('http://%s/horizon' % (dashboard_ip))
        html = response.read()
        if 'OpenStack Dashboard' not in html:
            msg="Dashboard frontpage check failed"
            amulet.raise_status(amulet.FAIL, msg=msg)
