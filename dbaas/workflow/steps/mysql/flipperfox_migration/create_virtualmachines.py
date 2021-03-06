# -*- coding: utf-8 -*-
import logging
from util import get_credentials_for
from util import full_stack
from dbaas_cloudstack.provider import CloudStackProvider
from dbaas_credentials.models import CredentialType
from dbaas_cloudstack.models import PlanAttr
from dbaas_cloudstack.models import HostAttr
from dbaas_cloudstack.models import LastUsedBundle
from dbaas_cloudstack.models import LastUsedBundleDatabaseInfra
from physical.models import Host
from physical.models import Instance
from workflow.steps.util.base import BaseStep
from workflow.exceptions.error_codes import DBAAS_0020

LOG = logging.getLogger(__name__)


class CreateVirtualMachine(BaseStep):

    def __unicode__(self):
        return "Creating virtualmachines..."

    def do(self, workflow_dict):
        try:

            cs_credentials = get_credentials_for(
                environment=workflow_dict['environment'],
                credential_type=CredentialType.CLOUDSTACK)

            vm_credentials = get_credentials_for(
                environment=workflow_dict['environment'],
                credential_type=CredentialType.VM)

            cs_provider = CloudStackProvider(credentials=cs_credentials)

            offering = workflow_dict['offering']

            cs_plan_attrs = PlanAttr.objects.get(
                plan=workflow_dict['target_plan']
            )

            workflow_dict['target_hosts'] = []
            workflow_dict['target_instances'] = []

            workflow_dict['target_plan'].validate_min_environment_bundles()
            bundles = list(cs_plan_attrs.bundle.filter(is_active=True))

            for index, source_instance in enumerate(workflow_dict['source_instances']):

                source_host = workflow_dict['source_hosts'][index]
                vm_name = source_host.hostname.split('.')[0]
                source_host_attr = HostAttr.objects.get(host=source_host)
                source_network_id = cs_provider.get_vm_network_id(
                    vm_id=source_host_attr.vm_id,
                    project_id=cs_credentials.project)

                if len(bundles) == 1:
                    bundle = bundles[0]
                else:
                    if index == 0:
                        bundle = LastUsedBundle.get_next_infra_bundle(
                            plan=workflow_dict['target_plan'], bundles=bundles)
                    else:
                        bundle = LastUsedBundle.get_next_bundle(
                            current_bundle=bundle, bundles=bundles)

                    if bundle.networkid == source_network_id:
                        bundle = LastUsedBundle.get_next_bundle(
                            current_bundle=bundle, bundles=bundles)

                LOG.debug(
                    "Deploying new vm on cs with bundle %s and offering %s" % (bundle, offering))

                vm = cs_provider.deploy_virtual_machine(offering=offering.serviceofferingid,
                                                        bundle=bundle,
                                                        project_id=cs_credentials.project,
                                                        vmname=vm_name,
                                                        affinity_group_id=cs_credentials.get_parameter_by_name(
                                                            'affinity_group_id'),
                                                        )

                if not vm:
                    raise Exception(
                        "CloudStack could not create the virtualmachine")

                host = Host()
                host.address = vm['virtualmachine'][0]['nic'][0]['ipaddress']
                host.hostname = host.address
                host.save()
                workflow_dict['target_hosts'].append(host)

                source_host.future_host = host
                source_host.save()

                host_attr = HostAttr()
                host_attr.vm_id = vm['virtualmachine'][0]['id']
                host_attr.vm_user = vm_credentials.user
                host_attr.vm_password = vm_credentials.password
                host_attr.host = host
                host_attr.bundle = bundle
                host_attr.save()
                LOG.info("Host attrs custom attributes created!")

                instance = Instance()
                instance.address = host.address
                instance.dns = host.address
                instance.port = source_instance.port

                instance.is_active = source_instance.is_active
                instance.instance_type = source_instance.instance_type
                instance.hostname = host

                instance.databaseinfra = workflow_dict['databaseinfra']
                instance.save()
                LOG.info("Instance created!")

                source_instance.future_instance = instance
                source_instance.save()

                workflow_dict['target_instances'].append(instance)

                databaseinfra = workflow_dict['databaseinfra']
                databaseinfra.last_vm_created += 1
                databaseinfra.save()
                workflow_dict['databaseinfra'] = databaseinfra

                LastUsedBundleDatabaseInfra.set_last_infra_bundle(
                    databaseinfra=databaseinfra, bundle=host_attr.bundle)

            return True
        except Exception:
            traceback = full_stack()

            workflow_dict['exceptions']['error_codes'].append(DBAAS_0020)
            workflow_dict['exceptions']['traceback'].append(traceback)

            return False

    def undo(self, workflow_dict):
        LOG.info("Running undo...")
        try:
            cs_credentials = get_credentials_for(
                environment=workflow_dict['environment'],
                credential_type=CredentialType.CLOUDSTACK)

            cs_provider = CloudStackProvider(credentials=cs_credentials)

            for source_instance in workflow_dict['source_instances']:
                source_instance.future_instance = None
                source_instance.save()
                LOG.info("Source instance updated")

            for target_instance in workflow_dict['target_instances']:
                target_instance.delete()
                LOG.info("Target instance deleted")

            for source_host in workflow_dict['source_hosts']:
                source_host.future_host = None
                source_host.save()
                LOG.info("Source host updated")

            for target_host in workflow_dict['target_hosts']:
                host_attr = HostAttr.objects.get(host=target_host)
                LOG.info("Destroying virtualmachine %s" % host_attr.vm_id)

                cs_provider.destroy_virtual_machine(
                    project_id=cs_credentials.project,
                    environment=workflow_dict['environment'],
                    vm_id=host_attr.vm_id)

                host_attr.delete()
                LOG.info("HostAttr deleted!")

                target_host.delete()
                LOG.info("Target host deleted")

            return True
        except Exception:
            traceback = full_stack()

            workflow_dict['exceptions']['error_codes'].append(DBAAS_0020)
            workflow_dict['exceptions']['traceback'].append(traceback)

            return False
