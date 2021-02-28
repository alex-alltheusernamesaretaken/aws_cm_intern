import requests
import boto3

DRY_RUN_DEFAULT = False  # Sets the default dry run flag on the aws calls
GROUP_NAME_DEFAULT = 'atlassian_id'  # the default name of the security group we're updating
VERBOSE_DEFAULT = False  # default setting for verbosity
IPRANGE_URL_DEFAULT = "https://ip-ranges.atlassian.com/"


def lambda_handler(event, context):
    # set up some values using the arguments from event, otherwise use default values defined above
    dry_run = True if "DRY_RUN" in event and event["DRY_RUN"].lower() == "true" else DRY_RUN_DEFAULT
    verbose = True if "VERBOSE" in event and event["VERBOSE"].lower() == "true" else VERBOSE_DEFAULT
    group_name = GROUP_NAME_DEFAULT if "GROUP_NAME" not in event else event["GROUP_NAME"]
    iprange_url = IPRANGE_URL_DEFAULT if "IPRANGE_URL" not in event else event["IPRANGE_URL"]

    try:
        r = requests.get(iprange_url)  # make the request to the url
    except requests.RequestException as e:  # something went wrong with request
        print(e)
        raise e

    data = r.json()  # get a json object out of the request
    del r

    if 'items' not in data:  # make sure the json is formatted as we expect it
        return {"message": "Malformed JSON, aborting"}

    address = []

    for v in data['items']:
        if 'cidr' not in v:  # if the cidr ip is somehow missing, just skip this one
            continue
        address.append(v['cidr'])
    del data

    ec2 = boto3.client('ec2')

    try:
        response = ec2.describe_security_groups(
            Filters=[dict(Name="group-name", Values=[group_name])],
            DryRun=dry_run
        )
    except Exception as e:
        print(f"Error getting security groups: {e}")
        raise e

    group = {}
    if len(response["SecurityGroups"]) == 0:  # make sure the group exists
        if verbose:
            print("Security group "+group_name+" does note exist, attempting to create it...")
        try:  # make the group if it doesn't exist
            response = ec2.create_security_group(
                Description="CM Intern test security group",
                GroupName=group_name,
                DryRun=dry_run
            )
        except Exception as e:
            print(f"Security group did not exist, error creating security group: {e}")
            raise e
        group = response
    else:
        group = response["SecurityGroups"][0]
    del response

    # revoke all existing rules in the security group, so that we can add the new ones to update the group
    # don't bother deleting old permissions if there aren't any
    if "IpPermissions" in group and len(group["IpPermissions"]) > 0:
        if verbose:
            print("Old security group permissions:\n", group["IpPermissions"])
        try:
            ec2.revoke_security_group_ingress(
                GroupId=group["GroupId"],
                IpPermissions=group["IpPermissions"],
                DryRun=dry_run
            )
        except Exception as e:
            print(f"Error revoking security group rule: {e}")
            raise e

    # make new security group ip permissions based on prior ip range request
    permissions = []
    for v in address:
        ippermission = {
            "FromPort": 0,
            "IpProtocol": "tcp",
            "ToPort": 65535,
            "IpRanges": [],
            "Ipv6Ranges": []
        }

        rangek = "IpRanges"
        cidrk = "CidrIp"
        if ":" in v:  # deal with ipv6 addresses
            rangek = "Ipv6Ranges"
            cidrk = "CidrIpv6"

        ippermission[rangek] = [{
            cidrk: v,
            "Description": group_name + " " + v
        }]

        permissions.append(ippermission)

    if verbose:
        print("Authorizing new security group permissions:\n", permissions)

    # authorize the new permissions
    try:
        ec2.authorize_security_group_ingress(
            GroupId=group["GroupId"],
            IpPermissions=permissions,
            DryRun=dry_run
        )
    except Exception as e:
        print(f"Error authorizing security group rules: {e}")
        raise e

    return {"message": "successfully updated "+str(len(permissions))+" security group rules"}
