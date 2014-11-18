#!/usr/bin/python
# vim: set ts=4:et

import sys
import os
from charmhelpers.core.hookenv import (
    Hooks, UnregisteredHookError,
    log,
    open_port,
    config,
    relation_set,
    relation_get,
    relation_ids,
    relations_of_type,
    local_unit,
    unit_get
)
from charmhelpers.fetch import (
    apt_update, apt_install,
    filter_installed_packages,
)
from charmhelpers.core.host import (
    restart_on_change
)
from charmhelpers.contrib.openstack.utils import (
    configure_installation_source,
    openstack_upgrade_available,
    os_release,
    save_script_rc
)
from horizon_utils import (
    PACKAGES, register_configs,
    restart_map,
    services,
    LOCAL_SETTINGS, HAPROXY_CONF,
    enable_ssl,
    do_openstack_upgrade,
    setup_ipv6
)
from charmhelpers.contrib.network.ip import (
    get_iface_for_address,
    get_netmask_for_address,
    get_ipv6_addr,
    is_ipv6
)
from charmhelpers.contrib.hahelpers.apache import install_ca_cert
from charmhelpers.contrib.hahelpers.cluster import get_hacluster_config
from charmhelpers.payload.execd import execd_preinstall
from charmhelpers.contrib.charmsupport import nrpe
from base64 import b64decode

hooks = Hooks()
CONFIGS = register_configs()


@hooks.hook('install')
def install():
    configure_installation_source(config('openstack-origin'))
    apt_update(fatal=True)
    packages = PACKAGES[:]
    if os_release('openstack-dashboard') < 'icehouse':
        packages += ['nodejs', 'node-less']
    apt_install(filter_installed_packages(packages), fatal=True)


@hooks.hook('upgrade-charm')
@restart_on_change(restart_map())
def upgrade_charm():
    execd_preinstall()
    apt_install(filter_installed_packages(PACKAGES), fatal=True)
    update_nrpe_config()
    CONFIGS.write_all()


@hooks.hook('config-changed')
@restart_on_change(restart_map())
def config_changed():
    if config('prefer-ipv6'):
        setup_ipv6()
        localhost = 'ip6-localhost'
    else:
        localhost = 'localhost'

    # Ensure default role changes are propagated to keystone
    for relid in relation_ids('identity-service'):
        keystone_joined(relid)
    enable_ssl()
    if openstack_upgrade_available('openstack-dashboard'):
        do_openstack_upgrade(configs=CONFIGS)

    env_vars = {
        'OPENSTACK_URL_HORIZON':
        "http://{}:70{}|Login+-+OpenStack".format(
            localhost,
            config('webroot')
        ),
        'OPENSTACK_SERVICE_HORIZON': "apache2",
        'OPENSTACK_PORT_HORIZON_SSL': 433,
        'OPENSTACK_PORT_HORIZON': 70
    }
    save_script_rc(**env_vars)
    update_nrpe_config()
    CONFIGS.write_all()
    open_port(80)
    open_port(443)


@hooks.hook('identity-service-relation-joined')
def keystone_joined(rel_id=None):
    relation_set(relation_id=rel_id,
                 service="None",
                 region="None",
                 public_url="None",
                 admin_url="None",
                 internal_url="None",
                 requested_roles=config('default-role'))


@hooks.hook('identity-service-relation-changed')
@restart_on_change(restart_map())
def keystone_changed():
    CONFIGS.write(LOCAL_SETTINGS)
    if relation_get('ca_cert'):
        install_ca_cert(b64decode(relation_get('ca_cert')))


@hooks.hook('cluster-relation-joined')
def cluster_joined(relation_id=None):
    if config('prefer-ipv6'):
        private_addr = get_ipv6_addr(exc_list=[config('vip')])[0]
        relation_set(relation_id=relation_id,
                     relation_settings={'private-address': private_addr})


@hooks.hook('cluster-relation-departed',
            'cluster-relation-changed')
@restart_on_change(restart_map())
def cluster_relation():
    CONFIGS.write(HAPROXY_CONF)


@hooks.hook('ha-relation-joined')
def ha_relation_joined():
    cluster_config = get_hacluster_config()
    resources = {
        'res_horizon_haproxy': 'lsb:haproxy'
    }

    resource_params = {
        'res_horizon_haproxy': 'op monitor interval="5s"'
    }

    vip_group = []
    for vip in cluster_config['vip'].split():
        if is_ipv6(vip):
            res_vip = 'ocf:heartbeat:IPv6addr'
            vip_params = 'ipv6addr'
        else:
            res_vip = 'ocf:heartbeat:IPaddr2'
            vip_params = 'ip'

        iface = get_iface_for_address(vip)
        if iface is not None:
            vip_key = 'res_horizon_{}_vip'.format(iface)
            resources[vip_key] = res_vip
            resource_params[vip_key] = (
                'params {ip}="{vip}" cidr_netmask="{netmask}"'
                ' nic="{iface}"'.format(ip=vip_params,
                                        vip=vip,
                                        iface=iface,
                                        netmask=get_netmask_for_address(vip))
            )
            vip_group.append(vip_key)

    if len(vip_group) > 1:
        relation_set(groups={'grp_horizon_vips': ' '.join(vip_group)})

    init_services = {
        'res_horizon_haproxy': 'haproxy'
    }
    clones = {
        'cl_horizon_haproxy': 'res_horizon_haproxy'
    }
    relation_set(init_services=init_services,
                 corosync_bindiface=cluster_config['ha-bindiface'],
                 corosync_mcastport=cluster_config['ha-mcastport'],
                 resources=resources,
                 resource_params=resource_params,
                 clones=clones)


@hooks.hook('website-relation-joined')
def website_relation_joined():
    relation_set(port=70,
                 hostname=unit_get('private-address'))


@hooks.hook('nrpe-external-master-relation-joined',
            'nrpe-external-master-relation-changed')
def update_nrpe_config():
    # Find out if nrpe set nagios_hostname
    hostname = None
    host_context = None
    for rel in relations_of_type('nrpe-external-master'):
        if 'nagios_hostname' in rel:
            hostname = rel['nagios_hostname']
            host_context = rel['nagios_host_context']
            break
    nrpe_compat = nrpe.NRPE(hostname=hostname)

    if host_context:
        current_unit = "%s:%s" % (host_context, local_unit())
    else:
        current_unit = local_unit()

    conf = nrpe_compat.config
    check_http_params = conf.get('nagios_check_http_params')
    if check_http_params:
        nrpe_compat.add_check(
            shortname='vhost',
            description='Check Virtual Host {%s}' % current_unit,
            check_cmd='check_http %s' % check_http_params
        )

    services_to_monitor = services()
    for service in services_to_monitor:
        upstart_init = '/etc/init/%s.conf' % service
        sysv_init = '/etc/init.d/%s' % service

        if os.path.exists(upstart_init):
            nrpe_compat.add_check(
                shortname=service,
                description='process check {%s}' % current_unit,
                check_cmd='check_upstart_job %s' % check_http_params
                )
        elif os.path.exists(sysv_init):
            cronpath = '/etc/cron.d/nagios-service-check-%s' % service
            cron_template = '*/5 * * * * root \
/usr/local/lib/nagios/plugins/check_exit_status.pl -s /etc/init.d/%s \
status > /var/lib/nagios/service-check-%s.txt\n' % (service, service)
            f = open(cronpath, 'w')
            f.write(cron_template)
            f.close()
            nrpe_compat.add_check(
                shortname=service,
                description='process check {%s}' % current_unit,
                check_cmd='check_status_file.py -f \
/var/lib/nagios/service-check-%s.txt' % service,
                )

    nrpe_compat.write()


def main():
    try:
        hooks.execute(sys.argv)
    except UnregisteredHookError as e:
        log('Unknown hook {} - skipping.'.format(e))


if __name__ == '__main__':
    main()
