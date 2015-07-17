#!/usr/bin/python

import amulet
import os
import time
import urllib2
import yaml

from charmhelpers.contrib.openstack.amulet.deployment import (
    OpenStackAmuletDeployment
)

from charmhelpers.contrib.openstack.amulet.utils import (
    OpenStackAmuletUtils,
    DEBUG, # flake8: noqa
    ERROR
)

# Use DEBUG to turn on debug logging
u = OpenStackAmuletUtils(DEBUG)


class OpenstackDashboardBasicDeployment(OpenStackAmuletDeployment):
    """Amulet tests on a basic openstack-dashboard deployment."""

    def __init__(self, series, openstack=None, source=None, git=False,
                 stable=False):
        """Deploy the entire test environment."""
        super(OpenstackDashboardBasicDeployment, self).__init__(series, openstack,
                                                            source, stable)
        self.git = git
        self._add_services()
        self._add_relations()
        self._configure_services()
        self._deploy()
        self._initialize_tests()

    def _add_services(self):
        """Add services

           Add the services that we're testing, where openstack-dashboard is local,
           and the rest of the service are from lp branches that are
           compatible with the local charm (e.g. stable or next).
           """
        this_service = {'name': 'openstack-dashboard'}
        other_services = [{'name': 'keystone'}, {'name': 'mysql'}]
        super(OpenstackDashboardBasicDeployment, self)._add_services(this_service,
                                                                     other_services)

    def _add_relations(self):
        """Add all of the relations for the services."""
        relations = {
          'openstack-dashboard:identity-service': 'keystone:identity-service',
          'keystone:shared-db': 'mysql:shared-db',
        }
        super(OpenstackDashboardBasicDeployment, self)._add_relations(relations)

    def _configure_services(self):
        """Configure all of the services."""
        horizon_config = {}
        if self.git:
            amulet_http_proxy = os.environ.get('AMULET_HTTP_PROXY')

            reqs_repo = 'git://github.com/openstack/requirements'
            horizon_repo = 'git://github.com/openstack/horizon'
            if self._get_openstack_release() == self.trusty_icehouse:
                reqs_repo = 'git://github.com/coreycb/requirements'
                horizon_repo = 'git://github.com/coreycb/horizon'

            branch = 'stable/' + self._get_openstack_release_string()

            openstack_origin_git = {
                'repositories': [
                    {'name': 'requirements',
                     'repository': reqs_repo, 
                     'branch': branch},
                    {'name': 'horizon',
                     'repository': horizon_repo,
                     'branch': branch},
                ],
                'directory': '/mnt/openstack-git',
                'http_proxy': amulet_http_proxy,
                'https_proxy': amulet_http_proxy,
            }
            horizon_config['openstack-origin-git'] = yaml.dump(openstack_origin_git)

        keystone_config = {'admin-password': 'openstack',
                           'admin-token': 'ubuntutesting'}
        mysql_config = {'dataset-size': '50%'}
        configs = {'openstack-dashboard': horizon_config,
                   'mysql': mysql_config,
                   'keystone': keystone_config}
        super(OpenstackDashboardBasicDeployment, self)._configure_services(configs)

    def _initialize_tests(self):
        """Perform final initialization before tests get run."""
        # Access the sentries for inspecting service units
        self.keystone_sentry = self.d.sentry.unit['keystone/0']
        self.openstack_dashboard_sentry = self.d.sentry.unit['openstack-dashboard/0']

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

    def crude_py_parse(self, file_contents, expected):
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
        self.crude_py_parse(file_contents, expected)

    def test_router_settings(self):
        if self.openstack > "icehouse":
            unit = self.openstack_dashboard_sentry
            conf = ('/usr/share/openstack-dashboard/openstack_dashboard/'
                    'enabled/_40_router.py')
            file_contents = unit.file_contents(conf)
            expected = {
                'DISABLED': "True",
            }
            self.crude_py_parse(file_contents, expected)

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

    def test_z_restart_on_config_change(self):
        """Verify that the specified services are restarted when the config
           is changed.

           Note(coreycb): The method name with the _z_ is a little odd
           but it forces the test to run last.  It just makes things
           easier because restarting services requires re-authorization.
           """
        conf = '/etc/openstack-dashboard/local_settings.py'
        services = ['apache2']
        self.d.configure('openstack-dashboard', {'use-syslog': 'True'})
        time = 60
        for s in services:
            if not u.service_restarted(self.openstack_dashboard_sentry, s, conf,
                                       pgrep_full=True, sleep_time=time):
                self.d.configure('openstack-dashboard', {'use-syslog': 'False'})
                msg = "service {} didn't restart after config change".format(s)
            time = 0
        self.d.configure('openstack-dashboard', {'use-syslog': 'False'})

    def test_openstack_dashboard_identity_service_relation(self):
        """Verify the openstack-dashboard to keystone identity-service relation data."""
        unit = self.openstack_dashboard_sentry
        relation = ['identity-service', 'keystone:identity-service']
        expected = {
            'private-address': u.valid_ip,
            'requested_roles': 'Member',
        }

        ret = u.validate_relation_data(unit, relation, expected)
        if ret:
            message = u.relation_error('openstack-dashboard identity-service', ret)
            amulet.raise_status(amulet.FAIL, msg=message)

    def test_keystone_identity_service_relation(self):
        """Verify the keystone to openstack-dashboard identity-service relation data."""
        unit = self.keystone_sentry
        relation = ['identity-service', 'openstack-dashboard:identity-service']
        expected = {
            'auth_host': u.valid_ip,
            'auth_port': '35357',
            'auth_protocol': 'http',
            'private-address': u.valid_ip,
            'region': 'RegionOne',
            'service_host': u.valid_ip,
            'service_port': '5000',
            'service_protocol': 'http',
        }

        ret = u.validate_relation_data(unit, relation, expected)
        if ret:
            message = u.relation_error('keystone identity-service', ret)
            amulet.raise_status(amulet.FAIL, msg=message)
