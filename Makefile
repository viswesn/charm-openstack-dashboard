#!/usr/bin/make
PYTHON := /usr/bin/env python

lint:
	@flake8 --exclude hooks/charmhelpers hooks unit_tests
	@charm proof

test:
	@echo Starting tests...
	@$(PYTHON) /usr/bin/nosetests --nologcapture unit_tests

sync:
	@charm-helper-sync -c charm-helpers-sync.yaml

publish:
	bzr push lp:charms/openstack-dashboard
	bzr push lp:charms/trusty/openstack-dashboard
