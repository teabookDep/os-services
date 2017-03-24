#!/usr/bin/env python
#
# Copyright 2016, 2016 IBM US, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import argparse
import sys
import yaml

SWIFT = 'swift'
SWIFT_MIN = 'swift-minimum-hardware'
CEPH = 'ceph-standalone'
DBAAS = 'dbaas'
COMPUTE = 'private-compute-cloud'
BASE_ARCHS = {SWIFT, COMPUTE, CEPH}


class UnsupportedConfig(Exception):
    pass


class InvalidDeviceList(Exception):
    pass


def validate(file_path):
    try:
        inventory = _load_yml(file_path)
        validate_reference_architecture(inventory)
        validate_private_compute_cloud(inventory)
        validate_swift(inventory)
        validate_ceph(inventory)
        validate_ops_mgr(inventory)
    except Exception as ex:
        print ex
        sys.exit(1)


def validate_reference_architecture(inventory):
    reference_architecture = inventory.get('reference-architecture')
    if not reference_architecture:
        raise UnsupportedConfig('Missing reference-architecture setting.')

    # Validate that we have at least one base architecture in the list
    if len(BASE_ARCHS.intersection(reference_architecture)) == 0:
        raise UnsupportedConfig('Missing base architecture')

    if (DBAAS in reference_architecture and
            COMPUTE not in reference_architecture):
        raise UnsupportedConfig('dbaas cannot be used without '
                                'private-compute-cloud.')

    if (SWIFT_MIN in reference_architecture and
            'swift' not in reference_architecture):
        raise UnsupportedConfig('swift-minimum-hardware cannot be used alone')

    # Validate ceph standalone is alone
    if CEPH in reference_architecture and len(reference_architecture) != 1:
        raise UnsupportedConfig('The ceph-standalone reference architecture '
                                'cannot be used in conjunction with other '
                                'reference architectures.')


def _is_network_existing(templates, required_net):
    """ Check if a required network exists in all the given node templates
    """
    if not templates:
        return True

    for template in templates:
        nets = template.get('networks', [])
        if required_net not in nets:
            return False

    return True


def _validate_private_compute_cloud_node_templates(roles_to_templates):
    """ Validate that there should be at least one controller node template
        and one compute node template when private-compute-cloud reference
        architecture is there
    """
    # Both 'controller' and 'controllers' can be keys in the dictionary
    # roles_to_templates because 'controllers' is the old node template name
    # and 'controller' is the role name.
    if not roles_to_templates.get('controller') and \
       not roles_to_templates.get('controllers'):
        msg = ('The configuration must either have a node template named '
               '\'controllers\' or one node template which has the '
               'controller role.')
        raise UnsupportedConfig(msg)

    if not roles_to_templates.get('compute'):
        msg = ('The configuration must either have a node template named '
               '\'compute\' or one node template which has the compute role.')
        raise UnsupportedConfig(msg)


def _validate_private_compute_cloud_networks(config, roles_to_templates):
    """ Validate that the networks required for private-compute-cloud exist
    """
    # These networks should be configured on controllers and compute nodes
    required_nets = ['openstack-mgmt', 'openstack-tenant-vxlan',
                     'openstack-tenant-vlan', 'openstack-stg']

    for required_net in required_nets:
        if required_net not in config.get('networks', []):
            msg = ('The required network %s is missing.' % required_net)
            raise UnsupportedConfig(msg)

    # Validate that the controller node templates have the required networks
    for required_net in required_nets:
        if not _is_network_existing(roles_to_templates.get('controller'),
                                    required_net):
            msg = ('Missing network %(net)s in a node template with '
                   'controller role')
            raise UnsupportedConfig(msg % {'net': required_net})
        if not _is_network_existing(roles_to_templates.get('controllers'),
                                    required_net):
            msg = ('Missing network %(net)s in the controllers node template')
            raise UnsupportedConfig(msg % {'net': required_net})

    # Validate that the compute node templates have the required networks
    for required_net in required_nets:
        if not _is_network_existing(roles_to_templates.get('compute'),
                                    required_net):
            msg = ('Missing network %(net)s in a compute node template')
            raise UnsupportedConfig(msg % {'net': required_net})


def validate_private_compute_cloud(inventory):
    """ Validate private-compute-cloud reference architecture configuration
    """

    reference_architecture = inventory.get('reference-architecture', [])
    if 'private-compute-cloud' not in reference_architecture:
        return

    roles_to_templates = _get_roles_to_templates(inventory)
    _validate_private_compute_cloud_node_templates(roles_to_templates)
    _validate_private_compute_cloud_networks(inventory, roles_to_templates)


def validate_swift(inventory):
    # We only support these layouts for Swift nodes and services:
    # proxy, metadata, object nodes with ring data set appropriately
    # proxy, converged object and metadata
    # if swift-minimum-hardware is specified we must have no proxy nodes,
    # no metadata nodes specified, and object servers must be converged

    reference_architecture = inventory.get('reference-architecture', [])
    if 'swift' not in reference_architecture:
        return

    converged_metadata_object = _has_converged_metadata_object(inventory)
    separate_metadata_object = _has_separate_metadata_object(inventory)
    if SWIFT_MIN in reference_architecture:
        if 'swift-proxy' in inventory.get('node-templates'):
            msg = ('The swift-proxy node template must not be used with the '
                   'swift-minimum-hardware reference architecture.')
            raise UnsupportedConfig(msg)
        if not converged_metadata_object:
            msg = ('When the swift-minimum-hardawre reference architecture is '
                   'specified, the account, container, and object rings must '
                   'be converged in the swift-object node template.')
            raise UnsupportedConfig(msg)
    else:
        if 'swift-proxy' not in inventory.get('node-templates'):
            msg = 'The swift-proxy node template was not found.'
            raise UnsupportedConfig(msg)

        if not (converged_metadata_object or separate_metadata_object):
            msg = ('The configuration of the swift-metadata, and swift-object '
                   'nodes and their corresponding account, container, and '
                   'object rings organization is not supported.')
            raise UnsupportedConfig(msg)


def _has_converged_metadata_object(inventory):
    # Return true only if:
    # object template and no metadata template
    # and container, account, and object settings are all on object template
    swift_meta = inventory['node-templates'].get('swift-metadata')
    swift_obj = inventory['node-templates'].get('swift-object')
    if swift_obj and not swift_meta:
        required_props = {'account-ring-devices',
                          'container-ring-devices',
                          'object-ring-devices'}
        domain_settings = swift_obj.get('domain-settings', {})
        if required_props.issubset(domain_settings.keys()):
            return True

    return False


def _has_separate_metadata_object(inventory):
    # Return true only if:
    # both object and metadata templates
    # metadata has account and container ring config and no object config
    # object has object but not account and container
    swift_meta = inventory['node-templates'].get('swift-metadata')
    swift_obj = inventory['node-templates'].get('swift-object')
    if swift_obj and swift_meta:
        required_meta_props = {'account-ring-devices',
                               'container-ring-devices'}
        meta_settings = swift_meta.get('domain-settings', {})
        obj_settings = swift_obj.get('domain-settings', {})
        if (required_meta_props.issubset(meta_settings.keys()) and
                'object-ring-devices' in obj_settings.keys()):
            return True
    return False


def validate_ceph(inventory):
    reference_architecture = inventory.get('reference-architecture')
    if (CEPH not in reference_architecture and
            COMPUTE not in reference_architecture):
        # Nothing to validate.  No ref archs that use Ceph.
        return

    roles_to_templates = _get_roles_to_templates(inventory)
    _validate_ceph_node_templates(roles_to_templates)
    _validate_ceph_networks(inventory, roles_to_templates)
    _validate_ceph_devices(inventory, roles_to_templates)


def _validate_ceph_node_templates(roles_to_templates):
    if not roles_to_templates.get('ceph-monitor'):
        msg = ('The configuration must either have a node template named '
               '\'controllers\' or one node template which has the '
               'ceph-monitor role.')
        raise UnsupportedConfig(msg)

    if not roles_to_templates.get('ceph-osd'):
        msg = ('The configuration must either have a node template named '
               '\'ceph-osd\' or one node template which has the '
               'ceph-osd role.')
        raise UnsupportedConfig(msg)


def _validate_ceph_networks(config, roles_to_templates):
    # Validate that the network used for Ceph public storage exists
    reference_architecture = config.get('reference-architecture')
    required_net = None
    if CEPH in reference_architecture:
        required_net = 'ceph-public-storage'
    else:  # COMPUTE
        required_net = 'openstack-stg'

    if required_net not in config.get('networks', []):
        msg = ('The required Ceph storage network %s is '
               'missing.' % required_net)
        raise UnsupportedConfig(msg)

    # Validate that the ceph monitor node templates
    # have the network
    if not _is_network_existing(roles_to_templates['ceph-monitor'],
                                required_net):
        msg = ('The ceph-monitor and/or controller node template(s)'
               ' are missing network %(net)s')
        raise UnsupportedConfig(msg % {'net': required_net})
    # Validate that the ceph osd node templates
    # have the network
    if not _is_network_existing(roles_to_templates['ceph-osd'], required_net):
        msg = ('The ceph osd node template(s) are missing network %(net)s')
        raise UnsupportedConfig(msg % {'net': required_net})


def _validate_ceph_devices(config, roles_to_templates):

    osd_templates = roles_to_templates.get('ceph-osd')
    for template in osd_templates:
        # Validate osd-devices is in domain-settings on the osd node template
        if not template.get('domain-settings', {}).get('osd-devices'):
            msg = 'A Ceph OSD node template is missing the osd-devices list.'
            raise UnsupportedConfig(msg)
    if len(osd_templates) > 1:
        # Validate that the osd-device lists match between the templates
        _validate_devices_lists(osd_templates, 'osd-devices')

        # If any node template has journal devices, validate that all have
        # journal devices.
        templates_with_journals = []
        for template in osd_templates:
            if 'journal-devices' in template.get('domain-settings', {}):
                templates_with_journals.append(template)
        if templates_with_journals and (len(templates_with_journals) !=
                                        len(osd_templates)):
            msg = ('When one Ceph OSD node template is specifying journal '
                   'devices, all Ceph OSD node templates must specify them.')
            raise UnsupportedConfig(msg)

        if templates_with_journals:
            _validate_devices_lists(osd_templates, 'journal-devices')


def _get_roles_to_templates(config):
    # Get a map of roles to node-templates
    # This includes the backward compatible support for role being the
    # template name and controller nodes being ceph-monitors.
    # Both 'controller' and 'controllers' can be keys in the resultant
    # dictionary roles_to_templates because 'controllers' is the old
    # node template name and 'controller' is the role name.

    def add_template(role, template, the_map):
        templates = the_map.get(role)
        if not templates:
            templates = []
            the_map[role] = templates
        if template not in templates:
            templates.append(template)

    roles_to_templates = {}
    if 'node-templates' not in config:
        msg = ('node-templates is missing in the configuration')
        raise UnsupportedConfig(msg)

    for name, template in config['node-templates'].iteritems():
        # Add the template by name
        add_template(name, template, roles_to_templates)
        if name == 'controllers':
            # Add as ceph-monitor role
            add_template('ceph-monitor', template, roles_to_templates)

        if 'roles' in template:
            for role in template['roles']:
                add_template(role, template, roles_to_templates)

    return roles_to_templates


def _validate_devices_lists(osd_templates, device_key):
    """
    Validates the device lists on the OSD templates.
    The journal device lists must match across the templates.
    The osd device lists must match across the templates.
    """

    def checkEqual(iterator):
        try:
            iterator = iter(iterator)
            first = next(iterator)
            return all(first == rest for rest in iterator)
        except StopIteration:
            return True

    are_equal = checkEqual(iter([tmpl['domain-settings'][device_key]
                                 for tmpl in osd_templates]))
    if not are_equal:
        msg = ('The device list %(list_name)s does not contain the same '
               'set of devices across all of '
               'the OSD nodes.') % {'list_name': device_key}
        raise InvalidDeviceList(msg)


def validate_ops_mgr(config):
    # Require that every node-template be connected to the openstack-mgmt
    # network
    required_net = 'openstack-mgmt'
    if required_net not in config.get('networks', []):
        msg = ('The required openstack-mgmt network %s is '
               'missing.' % required_net)
        raise UnsupportedConfig(msg)

    # validate that all the node templates have openstack-mgmt network
    for template_name, template in config.get('node-templates').iteritems():
        nets = template.get('networks', [])
        if required_net not in nets:
            msg = 'The node template %(template)s is missing network %(net)s'
            raise UnsupportedConfig(msg % {'template': template_name,
                                           'net': required_net})


def _load_yml(name):
    with open(name, 'r') as stream:
        try:
            return yaml.safe_load(stream)
        except yaml.YAMLError as ex:
            print(ex)
            sys.exit(1)


def main():

    parser = argparse.ArgumentParser(
        description=('Validate the config or inventory yaml file for '
                     'reference architectures.'),
        formatter_class=argparse.RawTextHelpFormatter)

    parser.add_argument('--file',
                        dest='file',
                        required=True,
                        help='The path to the config or inventory file.')

    # Handle error cases before attempting to parse
    # a command off the command line
    if len(sys.argv) == 1:
        parser.print_help()
        sys.exit(1)
    args = parser.parse_args()
    validate(args.file)

if __name__ == "__main__":
    main()
