#!/usr/bin/python
import sys

sys.path.append('hooks/')

from charmhelpers.contrib.openstack.utils import (
    do_action_openstack_upgrade,
)

from horizon_utils import (
    do_openstack_upgrade,
    register_configs
)


CONFIGS = register_configs()


def openstack_upgrade():
    """Upgrade packages to config-set Openstack version.

    If the charm was installed from source we cannot upgrade it.
    For backwards compatibility a config flag must be set for this
    code to run, otherwise a full service level upgrade will fire
    on config-changed."""

    do_action_openstack_upgrade('openstack-dashboard',
                                do_openstack_upgrade,
                                CONFIGS)

if __name__ == '__main__':
    openstack_upgrade()
