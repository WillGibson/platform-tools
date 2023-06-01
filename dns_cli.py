#!/usr/bin/env python

import click
import boto3
import botocore
import os
import yaml
import time

from utils.aws import check_response, check_aws_conn

# To do
# -----
# Check user has logged into the aws accounts before scanning the accounts
# When adding records from parent to subdomain, if ok, it then should remove them from parent domain
# (run a test before removing)

# Base domain depth
MAX_DOMAIN_DEPTH = 2
AWS_CERT_REGION = "eu-west-2"


def wait_for_certificate_validation(acm_client, certificate_arn, sleep_time=5, timeout=600):

    print("waiting for cert...")
    status = acm_client.describe_certificate(CertificateArn=certificate_arn)['Certificate']['Status']
    elapsed_time = 0
    while status == 'PENDING_VALIDATION':
        if elapsed_time > timeout:
            raise Exception(f'Timeout ({timeout}s) reached for certificate validation')
        print(f"{certificate_arn}: Waiting {sleep_time}s for validation, {elapsed_time}s elapsed...")
        time.sleep(sleep_time)
        status = acm_client.describe_certificate(CertificateArn=certificate_arn)['Certificate']['Status']
        elapsed_time += sleep_time
    print("cert validated...")


def create_cert(client, domain_client, domain, base_len):
    # Check if cert is present.
    arn = ""
    resp = client.list_certificates(
            CertificateStatuses=[
                'PENDING_VALIDATION', 'ISSUED', 'INACTIVE', 'EXPIRED', 'VALIDATION_TIMED_OUT', 'REVOKED', 'FAILED'
            ],
            MaxItems=500)

    # Need to check if cert status is issued, if pending need to update dns
    for cert in resp["CertificateSummaryList"]:
        if domain == cert['DomainName']:
            print("Cert already exists, do not need to create")
            return cert['CertificateArn']

    if not click.confirm(f"Creating Cert for {domain}\nDo you want to continue?"):
        exit()

    parts = domain.split(".")

    # We only want to create domains max 2 deep so remove all sub domains in excess
    parts_to_remove = len(parts) - base_len - MAX_DOMAIN_DEPTH
    domain_to_create = ".".join(parts[parts_to_remove:]) + "."
    print(domain_to_create)
    # cert_client = DNSValidatedACMCertClient(domain=domain, profile='intranet')
    response = client.request_certificate(
        DomainName=domain, ValidationMethod='DNS')

    arn = response['CertificateArn']

    # Create DNS validation records
    # Need a pause for it to populate the DNS resource records
    response = client.describe_certificate(
        CertificateArn=arn
    )
    while response['Certificate'].get('DomainValidationOptions') is None or \
            response['Certificate']['DomainValidationOptions'][0].get('ResourceRecord') is None:
        print("Waiting for DNS records...")
        time.sleep(2)
        response = client.describe_certificate(
            CertificateArn=arn
        )

    cert_record = response['Certificate']['DomainValidationOptions'][0]['ResourceRecord']
    response = domain_client.list_hosted_zones_by_name()

    for hz in response["HostedZones"]:
        if hz['Name'] == domain_to_create:
            domain_id = hz['Id']
            break

    # Add NS records of subdomain to parent
    print(domain_id)

    if not click.confirm(f"Updating DNS record for cert {domain}\nDo you want to continue?"):
        exit()

    response = domain_client.change_resource_record_sets(
        HostedZoneId=domain_id,
        ChangeBatch={
            'Changes': [{
                'Action': 'CREATE',
                'ResourceRecordSet': {
                    'Name': cert_record['Name'],
                    'Type': cert_record['Type'],
                    'TTL': 300,
                    'ResourceRecords': [{'Value': cert_record['Value']},],
                }
            }]
        }
    )

    # Wait for certificate to get to validation state before continuing
    wait_for_certificate_validation(client, certificate_arn=arn, sleep_time=5, timeout=600)

    return arn


def add_records(client, records, subdom_id, action):
    if records['Type'] == "A":
        response = client.change_resource_record_sets(
            HostedZoneId=subdom_id,
            ChangeBatch={
                'Comment': 'Record created for copilot',
                'Changes': [{
                    'Action': action,
                    'ResourceRecordSet': {
                        'Name': records['Name'],
                        'Type': records['Type'],
                        'AliasTarget': {
                            'HostedZoneId': records['AliasTarget']['HostedZoneId'],
                            'DNSName': records['AliasTarget']['DNSName'],
                            'EvaluateTargetHealth': records['AliasTarget']['EvaluateTargetHealth']
                        },
                    }
                }]
            }
        )
    else:
        response = client.change_resource_record_sets(
            HostedZoneId=subdom_id,
            ChangeBatch={
                'Comment': 'Record created for copilot',
                'Changes': [{
                    'Action': action,
                    'ResourceRecordSet': {
                        'Name': records['Name'],
                        'Type': records['Type'],
                        'TTL': records['TTL'],
                        'ResourceRecords': [{'Value': records['ResourceRecords'][0]['Value']},],
                    }
                }]
            }
        )

    check_response(response)
    print(f"{records['Name']}, Type: {records['Type']} Added.")


def check_for_records(client, parent_id, subdom, subdom_id):
    records_to_add = []
    response = client.list_resource_record_sets(
        HostedZoneId=parent_id,
    )

    for records in response['ResourceRecordSets']:
        if subdom in records['Name']:
            print(records['Name'], " found")
            records_to_add.append(records)
            add_records(client, records, subdom_id)
    return True


def create_hosted_zone(client, domain, start_domain, base_len):
    print("Creating new Zone")

    parts = domain.split(".")

    # We only want to create domains max 2 deep so remove all sub domains in excess
    parts_to_remove = len(parts) - base_len - MAX_DOMAIN_DEPTH
    domain_to_create = parts[parts_to_remove:]

    # Walk back domain name
    for x in reversed(range(len(domain_to_create) - (len(start_domain.split(".")) - 1))):
        subdom = ".".join(domain_to_create[(x):]) + "."

        if not click.confirm(f"Do you wish to create domain {subdom}...\nDo you want to continue?"):
            exit()

        parent = ".".join(subdom.split(".")[1:])
        response = client.list_hosted_zones_by_name()

        for hz in response["HostedZones"]:
            if hz['Name'] == parent:
                parent_id = hz['Id']
                break

        # update CallerReference to unique string eg date.
        response = client.create_hosted_zone(
            Name=subdom,
            CallerReference=f'{subdom}_from_code',
        )
        ns_records = response['DelegationSet']
        subdom_id = response['HostedZone']['Id']

        # Check if records existed in the parent domain, if so they need to be copied to sub domain.
        check_for_records(client, parent_id, subdom, subdom_id)

        if not click.confirm(f"Updating parent {parent} domain with records \
                             {ns_records['NameServers']}\nDo you want to continue?"):
            exit()

        # Add NS records of subdomain to parent
        nameservers = ns_records['NameServers']
        # append  . to make fqdn
        nameservers = ['{}.'.format(nameserver) for nameserver in nameservers]
        nameserver_resource_records = [{'Value': nameserver} for nameserver in nameservers]

        response = client.change_resource_record_sets(
            HostedZoneId=parent_id,
            ChangeBatch={
                'Changes': [{
                    'Action': 'CREATE',
                    'ResourceRecordSet': {
                        'Name': subdom,
                        'Type': 'NS',
                        'TTL': 300,
                        'ResourceRecords': nameserver_resource_records,
                    }
                }]
            }
        )


def check_r53(domain_session, project_session, domain, base_domain):
    # find the hosted zone
    domain_client = domain_session.client('route53')
    acm_client = project_session.client('acm', region_name=AWS_CERT_REGION)

    # create the certificate
    response = domain_client.list_hosted_zones_by_name()

    hosted_zones = {}
    for hz in response["HostedZones"]:
        hosted_zones[hz["Name"]] = hz

    # Check if base domain is valid
    if base_domain[-1] != ".":
        base_domain = base_domain + "."

    if base_domain not in hosted_zones:
        print(f"The base domain: {base_domain} does not exist in your AWS domain account")
        exit()

    base_len = (len(base_domain.split(".")) - 1)
    parts = domain.split(".")

    for _ in range(len(parts) - 1):
        subdom = ".".join(parts) + "."
        print(f"searching for {subdom}... ")
        if subdom in hosted_zones:
            print("Found hosted zone", hosted_zones[subdom]['Name'])

            # We only want to go 2 sub domains deep in R53
            if (len(parts) - base_len) < MAX_DOMAIN_DEPTH:
                print("Creating Hosted Zone")
                create_hosted_zone(domain_client, domain, subdom, base_len)

            break

        parts.pop(0)
    else:
        # This should only occur when base domain this needs is not found
        print(f"Root Domain not found for {domain}")
        return

    # add records to hosted zone to validate certificate
    cert_arn = create_cert(acm_client, domain_client, domain, base_len)

    return cert_arn


@click.group()
def cli():
    pass


@cli.command()
@click.option('--path', help='path of copilot folder', required=True)
@click.option('--domain-profile', help='aws account profile name for R53 domains account', required=True)
@click.option('--project-profile', help='aws account profile name for certificates account', required=True)
@click.option('--base-domain', help='root domain', required=True)
def check_domain(path, domain_profile, project_profile, base_domain):
    """
    Scans to see if Domain exists
    """

    domain_session = check_aws_conn(domain_profile)
    project_session = check_aws_conn(project_profile)

    if not os.path.exists(path):
        print("Please check path, manifest file not found")
        exit()

    if path.split(".")[-1] == "yml" or path.split(".")[-1] == "yaml":
        print("Please do not include the filename in the path")
        exit()

    cert_list = {}
    for root, dirs, files in os.walk(path):
        for file in files:
            if file == "manifest.yml" or file == "manifest.yaml":
                # Need to check that the manifest file is correctly configured.
                with open(os.path.join(root, file), "r") as fd:
                    conf = yaml.safe_load(fd)
                    if 'environments' in conf:
                        print("Checking file:")
                        print(os.path.join(root, file))
                        print("Domains listed in Manifest")

                        for env, domain in conf['environments'].items():
                            print("Env: ", env, " - Domain", domain['http']['alias'])
                            cert_arn = check_r53(domain_session, project_session, domain['http']['alias'], base_domain)
                            cert_list.update({domain['http']['alias']: cert_arn})
    if cert_list:
        print("\nHere are your Cert ARNs\n")
        for domain, cert in cert_list.items():
            print(f"Domain: {domain}\t - Cert ARN: {cert}")
    else:
        print("No domains found, please check the manifest file")


@cli.command()
@click.option('--app', help='Application Name', required=True)
@click.option('--domain-profile', help='aws account profile name for R53 domains account', required=True)
@click.option('--project-profile', help='aws account profile name for application account', required=True)
@click.option('--svc', help='Service Name', required=True)
@click.option('--env', help='Environment', required=True)
def assign_domain(app, domain_profile, project_profile, svc, env):
    """
    Check R53 domain is pointing to the correct ECS Load Blanacer
    """
    domain_session = check_aws_conn(domain_profile)
    project_session = check_aws_conn(project_profile)

    # Find the Load Balancer name.
    proj_client = project_session.client('ecs')

    response = proj_client.list_clusters()
    check_response(response)
    no_items = True
    for cluster_arn in response['clusterArns']:
        cluster_name = cluster_arn.split("/")[1]
        cluster_name_items = cluster_name.split("-")
        cluster_app = cluster_name_items[0]
        cluster_env = cluster_name_items[1]
        if cluster_app == app and cluster_env == env:
            no_items = False
            break

    if no_items:
        print("There are no matching clusters in this aws account")
        exit()

    response = proj_client.list_services(cluster=cluster_name)
    check_response(response)
    no_items = True
    for service_arn in response['serviceArns']:
        service_name = service_arn.split('/')[2]
        service_name_items = service_name.split("-")
        service_app = service_name_items[0]
        service_env = service_name_items[1]
        service_service = service_name_items[2]

        if service_app == app and service_env == env and service_service == svc:
            no_items = False
            break

    if no_items:
        print("There are no matching services in this aws account")
        exit()

    elb_client = project_session.client('elbv2')

    elb_arn = elb_client.describe_target_groups(TargetGroupArns=[
            proj_client.describe_services(cluster=cluster_name,
                                          services=[service_name,]
                                          )['services'][0]['loadBalancers'][0]['targetGroupArn']
            ])['TargetGroups'][0]['LoadBalancerArns'][0]

    response = elb_client.describe_load_balancers(LoadBalancerArns=[elb_arn])
    check_response(response)
    elb_name = response['LoadBalancers'][0]['DNSName']

    # Find the domain name
    response = elb_client.describe_listeners(LoadBalancerArn=[elb_arn][0])
    check_response(response)
    for listener in response['Listeners']:
        if listener['Protocol'] == 'HTTPS':
            acm_client = project_session.client('acm')
            response = acm_client.describe_certificate(
                CertificateArn=elb_client.describe_listener_certificates(ListenerArn=listener['ListenerArn'])
                ['Certificates'][0]['CertificateArn'])
            check_response(response)
            domain_name = response['Certificate']['DomainName']

    print(f"The Domain: {domain_name} \nhas been assigned the Load Balancer: {elb_name}\n\
          Checking to see if this is in R53")

    domain_client = domain_session.client('route53')
    sts_dom = domain_session.client('sts')

    # Display AWS Domain account details to ensure correct account
    alias_client = domain_session.client('iam')
    account_name = alias_client.list_account_aliases()['AccountAliases']
    print(f"Logged in with AWS Domain account: {account_name[0]}/{sts_dom.get_caller_identity()['Account']}\n\
          User: {sts_dom.get_caller_identity()['UserId']}")

    response = domain_client.list_hosted_zones_by_name()
    check_response(response)

    # Scan R53 Zone for matching domains and update records if needed.
    hosted_zones = {}
    for hz in response["HostedZones"]:
        hosted_zones[hz["Name"]] = hz

    parts = domain_name.split(".")
    for _ in range(len(parts) - 1):
        subdom = ".".join(parts) + "."
        print(f"searching for {subdom}... ")

        if subdom in hosted_zones:
            print("Found hosted zone", hosted_zones[subdom]['Name'])
            hosted_zone_id = hosted_zones[subdom]["Id"]

            # Does record existing
            response = domain_client.list_resource_record_sets(
                HostedZoneId=hosted_zone_id,
            )
            check_response(response)

            for record in response['ResourceRecordSets']:
                if domain_name == record['Name'][:-1]:
                    print(f"Record: {record['Name']} found")
                    print(f"is pointing to LB {record['ResourceRecords'][0]['Value']}")
                    if record['ResourceRecords'][0]['Value'] != elb_name:
                        if click.confirm(f"This doesnt match with the current LB {elb_name}, \
                                         Do you wish to update the record?"):
                            record = {"Name": domain_name, "Type": "CNAME", "TTL": 300,
                                      "ResourceRecords": [{"Value": elb_name}]}
                            add_records(domain_client, record, hosted_zone_id, "UPSERT")
                    else:
                        print("No need to add as it already exists")
                    exit()

            record = {"Name": domain_name, "Type": "CNAME", "TTL": 300, "ResourceRecords": [{"Value": elb_name}]}

            if not click.confirm(f"Creating R53 record: {record['Name']} -> {record['ResourceRecords'][0]['Value']}\n\
                                 In Domain: {subdom}\tZone ID: {hosted_zone_id}\nDo you want to continue?"):
                exit()
            add_records(domain_client, record, hosted_zone_id, "CREATE")
            exit()

        parts.pop(0)

    else:
        print(f"No hosted zone found for {domain_name}")
        return


if __name__ == "__main__":
    cli()