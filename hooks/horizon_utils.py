# vim: set ts=4:et
import grp
import horizon_contexts
import os
import pwd
import subprocess
import shutil
from collections import OrderedDict

import charmhelpers.contrib.openstack.context as context
import charmhelpers.contrib.openstack.templating as templating

from charmhelpers.contrib.openstack.utils import (
    configure_installation_source,
    get_os_codename_install_source,
    git_install_requested,
    git_clone_and_install,
    os_release,
    git_src_dir,
)
from charmhelpers.core.hookenv import (
    charm_dir,
    config,
    log
)
from charmhelpers.core.host import (
    adduser,
    add_group,
    add_user_to_group,
    cmp_pkgrevno,
    lsb_release,
    mkdir,
    service_restart,
)

from charmhelpers.fetch import (
    apt_upgrade,
    apt_update,
    add_source,
    apt_install
)

BASE_PACKAGES = [
    'haproxy',
    'memcached',
    'openstack-dashboard',
    'openstack-dashboard-ubuntu-theme',
    'python-keystoneclient',
    'python-memcache',
    'python-novaclient',
]

BASE_GIT_PACKAGES = [
    'apache2',
    'libapache2-mod-wsgi',
    'libpcre3-dev',
    'libxml2-dev',
    'libxslt1-dev',
    'python-dev',
    'python-pip',
    'python-setuptools',
    'zlib1g-dev',
]

# ubuntu packages that should not be installed when deploying from git
GIT_PACKAGE_BLACKLIST = [
    'openstack-dashboard',
    'openstack-dashboard-ubuntu-theme',
    'python-keystoneclient',
    'python-novaclient',
]

APACHE_CONF_DIR = "/etc/apache2"
LOCAL_SETTINGS = "/etc/openstack-dashboard/local_settings.py"
HAPROXY_CONF = "/etc/haproxy/haproxy.cfg"
APACHE_CONF = "%s/conf.d/openstack-dashboard.conf" % (APACHE_CONF_DIR)
APACHE_24_CONF = "%s/conf-available/openstack-dashboard.conf" \
    % (APACHE_CONF_DIR)
PORTS_CONF = "%s/ports.conf" % (APACHE_CONF_DIR)
APACHE_24_SSL = "%s/sites-available/default-ssl.conf" % (APACHE_CONF_DIR)
APACHE_24_DEFAULT = "%s/sites-available/000-default.conf" % (APACHE_CONF_DIR)
APACHE_SSL = "%s/sites-available/default-ssl" % (APACHE_CONF_DIR)
APACHE_DEFAULT = "%s/sites-available/default" % (APACHE_CONF_DIR)
ROUTER_SETTING = \
    "/usr/share/openstack-dashboard/openstack_dashboard/enabled/_40_router.py"

TEMPLATES = 'templates'

CONFIG_FILES = OrderedDict([
    (LOCAL_SETTINGS, {
        'hook_contexts': [horizon_contexts.HorizonContext(),
                          horizon_contexts.IdentityServiceContext(),
                          context.SyslogContext()],
        'services': ['apache2']
    }),
    (APACHE_CONF, {
        'hook_contexts': [horizon_contexts.HorizonContext(),
                          context.SyslogContext()],
        'services': ['apache2'],
    }),
    (APACHE_24_CONF, {
        'hook_contexts': [horizon_contexts.HorizonContext(),
                          context.SyslogContext()],
        'services': ['apache2'],
    }),
    (APACHE_SSL, {
        'hook_contexts': [horizon_contexts.ApacheSSLContext(),
                          horizon_contexts.ApacheContext()],
        'services': ['apache2'],
    }),
    (APACHE_24_SSL, {
        'hook_contexts': [horizon_contexts.ApacheSSLContext(),
                          horizon_contexts.ApacheContext()],
        'services': ['apache2'],
    }),
    (APACHE_DEFAULT, {
        'hook_contexts': [horizon_contexts.ApacheContext()],
        'services': ['apache2'],
    }),
    (APACHE_24_DEFAULT, {
        'hook_contexts': [horizon_contexts.ApacheContext()],
        'services': ['apache2'],
    }),
    (PORTS_CONF, {
        'hook_contexts': [horizon_contexts.ApacheContext()],
        'services': ['apache2'],
    }),
    (HAPROXY_CONF, {
        'hook_contexts': [horizon_contexts.HorizonHAProxyContext()],
        'services': ['haproxy'],
    }),
    (ROUTER_SETTING, {
        'hook_contexts': [horizon_contexts.RouterSettingContext()],
        'services': ['apache2'],
    }),
])


def register_configs():
    ''' Register config files with their respective contexts. '''
    release = os_release('openstack-dashboard')
    configs = templating.OSConfigRenderer(templates_dir=TEMPLATES,
                                          openstack_release=release)

    confs = [LOCAL_SETTINGS,
             HAPROXY_CONF,
             PORTS_CONF]

    for conf in confs:
        configs.register(conf, CONFIG_FILES[conf]['hook_contexts'])

    if os.path.isdir(APACHE_CONF_DIR) and cmp_pkgrevno('apache2', '2.4') >= 0:
        for conf in [APACHE_CONF, APACHE_SSL, APACHE_DEFAULT]:
            if os.path.isfile(conf):
                log('Removing old config %s' % (conf))
                os.remove(conf)
        configs.register(APACHE_24_DEFAULT,
                         CONFIG_FILES[APACHE_24_DEFAULT]['hook_contexts'])
        configs.register(APACHE_24_CONF,
                         CONFIG_FILES[APACHE_24_CONF]['hook_contexts'])
        configs.register(APACHE_24_SSL,
                         CONFIG_FILES[APACHE_24_SSL]['hook_contexts'])
    else:
        configs.register(APACHE_DEFAULT,
                         CONFIG_FILES[APACHE_DEFAULT]['hook_contexts'])
        configs.register(APACHE_CONF,
                         CONFIG_FILES[APACHE_CONF]['hook_contexts'])
        configs.register(APACHE_SSL,
                         CONFIG_FILES[APACHE_SSL]['hook_contexts'])

    if os.path.exists(os.path.dirname(ROUTER_SETTING)):
        configs.register(ROUTER_SETTING,
                         CONFIG_FILES[ROUTER_SETTING]['hook_contexts'])
    return configs


def restart_map():
    '''
    Determine the correct resource map to be passed to
    charmhelpers.core.restart_on_change() based on the services configured.

    :returns: dict: A dictionary mapping config file to lists of services
                    that should be restarted when file changes.
    '''
    _map = []
    for f, ctxt in CONFIG_FILES.iteritems():
        svcs = []
        for svc in ctxt['services']:
            svcs.append(svc)
        if svcs:
            _map.append((f, svcs))
    return OrderedDict(_map)


def services():
    ''' Returns a list of services associate with this charm '''
    _services = []
    for v in restart_map().values():
        _services = _services + v
    return list(set(_services))


def enable_ssl():
    ''' Enable SSL support in local apache2 instance '''
    subprocess.call(['a2ensite', 'default-ssl'])
    subprocess.call(['a2enmod', 'ssl'])


def determine_packages():
    """Determine packages to install"""
    packages = BASE_PACKAGES

    if git_install_requested():
        packages.extend(BASE_GIT_PACKAGES)
        # don't include packages that will be installed from git
        packages = list(set(packages))
        for p in GIT_PACKAGE_BLACKLIST:
            packages.remove(p)

    return list(set(packages))


def do_openstack_upgrade(configs):
    """
    Perform an upgrade.  Takes care of upgrading packages, rewriting
    configs, database migrations and potentially any other post-upgrade
    actions.

    :param configs: The charms main OSConfigRenderer object.
    """
    new_src = config('openstack-origin')
    new_os_rel = get_os_codename_install_source(new_src)

    log('Performing OpenStack upgrade to %s.' % (new_os_rel))

    configure_installation_source(new_src)
    dpkg_opts = [
        '--option', 'Dpkg::Options::=--force-confnew',
        '--option', 'Dpkg::Options::=--force-confdef',
    ]
    apt_update(fatal=True)
    apt_upgrade(options=dpkg_opts, fatal=True, dist=True)

    # set CONFIGS to load templates from new release
    configs.set_release(openstack_release=new_os_rel)


def setup_ipv6():
    ubuntu_rel = lsb_release()['DISTRIB_CODENAME'].lower()
    if ubuntu_rel < "trusty":
        raise Exception("IPv6 is not supported in the charms for Ubuntu "
                        "versions less than Trusty 14.04")

    # NOTE(xianghui): Need to install haproxy(1.5.3) from trusty-backports
    # to support ipv6 address, so check is required to make sure not
    # breaking other versions, IPv6 only support for >= Trusty
    if ubuntu_rel == 'trusty':
        add_source('deb http://archive.ubuntu.com/ubuntu trusty-backports'
                   ' main')
        apt_update()
        apt_install('haproxy/trusty-backports', fatal=True)


def git_install(projects_yaml):
    """Perform setup, and install git repos specified in yaml parameter."""
    if git_install_requested():
        git_pre_install()
        git_clone_and_install(projects_yaml, core_project='horizon')
        git_post_install(projects_yaml)


def git_pre_install():
    """Perform horizon pre-install setup."""
    dirs = [
        '/etc/openstack-dashboard',
        '/usr/share/openstack-dashboard',
        '/usr/share/openstack-dashboard/bin/less',
        '/usr/share/openstack-dashboard-ubuntu-theme/static/ubuntu/css',
        '/usr/share/openstack-dashboard-ubuntu-theme/static/ubuntu/img',
        '/usr/share/openstack-dashboard-ubuntu-theme/templates',
        '/var/lib/openstack-dashboard',
    ]

    adduser('horizon', shell='/bin/bash', system_user=True)
    subprocess.check_call(['usermod', '--home',
                          '/usr/share/openstack-dashboard/', 'horizon'])
    add_group('horizon', system_group=True)
    add_user_to_group('horizon', 'horizon')

    for d in dirs:
        if d is '/var/lib/openstack-dashboard':
            mkdir(d, owner='horizon', group='horizon', perms=0700, force=False)
        else:
            mkdir(d, owner='root', group='root', perms=0755, force=False)


def git_post_install(projects_yaml):
    """Perform horizon post-install setup."""
    src_dir = git_src_dir(projects_yaml, 'horizon')
    templates_dir = os.path.join(charm_dir(), 'templates/git')
    copy_files = {
        'manage': {
            'src': os.path.join(src_dir, 'manage.py'),
            'dest': '/usr/share/openstack-dashboard/manage.py',
        },
        'settings': {
            'src': os.path.join(src_dir, 'openstack_dashboard/settings.py'),
            'dest': '/usr/share/openstack-dashboard/settings.py',
        },
        'local_settings_example': {
            'src': os.path.join(src_dir, 'openstack_dashboard/local',
                                'local_settings.py.example'),
            'dest': '/etc/openstack-dashboard/local_settings.py',
        },
        'openstack-dashboard': {
            'src': os.path.join(templates_dir, 'dashboard.conf'),
            'dest': '/etc/apache2/conf-available/openstack-dashboard.conf',
        },
    }

    for name, files in copy_files.iteritems():
        if os.path.exists(files['dest']):
            os.remove(files['dest'])
        shutil.copyfile(files['src'], files['dest'])

    copy_trees = {
        'openstack_dashboard': {
            'src': os.path.join(src_dir, 'openstack_dashboard'),
            'dest': '/usr/share/openstack-dashboard/openstack_dashboard',
        },
    }

    for name, dirs in copy_trees.iteritems():
        if os.path.exists(dirs['dest']):
            shutil.rmtree(dirs['dest'])
        shutil.copytree(dirs['src'], dirs['dest'])

    share_dir = '/usr/share/openstack-dashboard/openstack_dashboard'
    symlinks = [
        {'src': '/usr/share/openstack-dashboard/openstack_dashboard/static',
         'link': '/usr/share/openstack-dashboard/static'},
        {'src': '/usr/bin/lessc',
         'link': '/usr/share/openstack-dashboard/bin/less/lessc'},
        {'src': '/etc/openstack-dashboard/local_settings.py',
         'link': os.path.join(share_dir, 'local/local_settings.py')},
        {'src':
            '/usr/local/lib/python2.7/dist-packages/horizon/static/horizon/',
         'link': os.path.join(share_dir, 'static/horizon')},
    ]

    for s in symlinks:
        if os.path.lexists(s['link']):
            os.remove(s['link'])
        os.symlink(s['src'], s['link'])

    os.chmod('/var/lib/openstack-dashboard', 0o750)
    os.chmod('/usr/share/openstack-dashboard/manage.py', 0o755),

    subprocess.check_call(['/usr/share/openstack-dashboard/manage.py',
                           'collectstatic', '--noinput'])
    subprocess.check_call(['/usr/share/openstack-dashboard/manage.py',
                           'compress', '--force'])

    uid = pwd.getpwnam('horizon').pw_uid
    gid = grp.getgrnam('horizon').gr_gid
    os.chown('/etc/openstack-dashboard', uid, gid)
    os.chown('/usr/share/openstack-dashboard/openstack_dashboard/static',
             uid, gid)
    os.chown('/var/lib/openstack-dashboard', uid, gid)

    static_dir = '/usr/share/openstack-dashboard/openstack_dashboard/static'
    for root, dirs, files in os.walk(static_dir):
        for d in dirs:
            os.lchown(os.path.join(root, d), uid, gid)
        for f in files:
            os.lchown(os.path.join(root, f), uid, gid)

    subprocess.check_call(['a2enconf', 'openstack-dashboard'])

    service_restart('apache2')


def git_post_install_late():
    """Perform horizon post-install setup."""
    subprocess.check_call(['/usr/share/openstack-dashboard/manage.py',
                           'collectstatic', '--noinput'])
    subprocess.check_call(['/usr/share/openstack-dashboard/manage.py',
                           'compress', '--force'])
