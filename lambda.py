# Copyright 2018 Calum Loudon
#
# Licensed under the Apache License, Version 2.0 (the "License"). You may not 
# use this file except in compliance with the License. A copy of the License
# is located at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# This file is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR 
# CONDITIONS OF ANY KIND, express or implied. See the License for the specific
# language governing permissions and limitations under the License.

# This file implements a lambda to implement the skill.  It supports the SHS
# API, both discvoery and subsequent defines a set of commands and associated
# Keene KIRA IR commands for a range of devices.

import logging
import time
import json
import pprint

from userDevices import DEVICES
from KIRAIO import SendToKIRA
from mapping import map_user_devices
from alexaSchema import DISCOVERY_RESPONSE, DIRECTIVE_RESPONSE
from utilities import verify_user, verify_request, get_uuid, get_utc_timestamp

# Logger boilerplate
logger = logging.getLogger()
logger.setLevel(logging.DEBUG)
fh = logging.FileHandler('kira.log')
fh.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
fh.setFormatter(formatter)
logger.addHandler(fh)
pp = pprint.PrettyPrinter(indent=2, width = 200)

TARGET = "cloudon7281.ddns.net"
PORT = 65432
REPEAT = 2
DELAY = 0.2
PAUSE_BETWEEN_COMMANDS = 0.4

def lambda_handler(request, context):
    # Main lambda handler.  We simply switch on the directive type.

    # XXX We should have something at this point that looks at the token in the
    # context and figures out what user it is for.  But I don't know how to do
    # that just yet, so ignore it for now and just assume it's me.
    user = "user1"
    logger.info("Request is for user %s", user)
    verify_user(user)

    # For now, map the user devices to endpoint and discovery information
    # on every request
    user_activities = map_user_devices(DEVICES[user]['devices'])
    pp.pprint(user_activities)

    try:
        logger.info("Directive:")
        logger.info(json.dumps(request, indent=4, sort_keys=True))

        if request["directive"]["header"]["name"] == "Discover":
            response = handle_discovery(user_activities['endpoints'])
        else:
            response = handle_non_discovery(request, user_activities['directive_responses'])

        logger.info("Response:")
        logger.info(json.dumps(response, indent=4, sort_keys=True))

        return response

    except ValueError as error:
        logger.error(error)
        raise

def handle_discovery(endpoints):
    # Handle discovery requests.  This is straightforward: we have already 
    # mapped the users set of devices to an auto-generated list of activities
    # (endpoints), so just return them.
    logger.info("It's a discovery")

    response = DISCOVERY_RESPONSE
    response['event']['payload']['endpoints'] = endpoints
    response['event']['header']['messageId'] = get_uuid()
                    
    return response

def handle_non_discovery(request, responses):
    # We have received a directive for some capability interface, which we have
    # to now act on.
    # The responses structure is a dict telling us what to do.  It is a nested
    # dict with the following structure:
    #
    # { endpoint:
    #     { interface:
    #         { directive:
    #             [ list of commands ]
    #         }
    #     }
    # }
    #
    # where the list of commands is a list of dicts with the following verbs
    # as keys:
    #
    #   SingleIRCommand     - send a single KIRA command; value is struct with 
    #                         IR sequence as value
    #   StepIRCommands      - send N * up/down KIRA commands; value is a struct
    #                         with +ve & -ve IR commands
    #   DigitsIRCommands    - send sequence of KIRA commands corresponding to 
    #                         digits of number in the payload; value is a struct
    #                         with IR commands for each decimal digit
    #   Pause               - pause for N seconds before sending next command;
    #                         time to wait is the value
    alexa_interface = request["directive"]["header"]["namespace"]
    # Strip off the 'Alexa.' at start of the string
    interface=alexa_interface[6:]
    directive = request["directive"]["header"]["name"]
    endpoint_id = request["directive"]["endpoint"]["endpointId"]

    logger.info("Received directive %s on interface %s for endpoint %s", directive, interface, endpoint_id)

    verify_request(responses, endpoint_id, interface, directive)

    commands_list = responses[endpoint_id][interface][directive]

    logger.info("Commands to execute:\n%s", pp.pformat(commands_list))

    for command_tuple in commands_list:
        for verb in command_tuple:
            logger.info("Verb to run: %s", verb)

            if verb == 'SingleIRCommand':
                # Send to KIRA the single command specified.
                KIRA_string = command_tuple[verb]['single']
                SendToKIRA(TARGET, PORT, KIRA_string, REPEAT, DELAY)

            elif verb == 'StepIRCommand':
                # In this case we need to extract the value N in the payload
                # then send either the +ve or -ve command N times.
                # XXX need to generalise payload location from AdjustVolume
                steps = request['directive']['payload']['volumeSteps']
                logger.info("Adjustment to make: %d", steps)

                if steps > 0:
                    KIRA_string = command_tuple[verb]['+ve']
                else:
                    KIRA_string = command_tuple[verb]['-ve']

                for n in range(0, abs(steps)-1):
                    SendToKIRA(TARGET, PORT, KIRA_string, REPEAT, DELAY)
                    time.sleep(PAUSE_BETWEEN_COMMANDS)

            elif verb == 'DigitsIRCommand':
                # In this case we need to extract a decimal number in the 
                # payload then send the sequence of IR commands corresponding
                # to its digits.
                # XXX need to generalise payload location from ChangeChannel
                number = request['directive']['payload']['channel']['number']
                logger.info("Number to send: %s", number)

                for digit in number:
                    KIRA_string = command_tuple[verb][digit]
                    SendToKIRA(TARGET, PORT, KIRA_string, REPEAT, DELAY)
                    time.sleep(PAUSE_BETWEEN_COMMANDS)

            elif verb == 'Pause':
                # Simply pause the appropriate period of time.
                time.sleep(command_tuple[verb])

        time.sleep(PAUSE_BETWEEN_COMMANDS)        
                   
    response = DIRECTIVE_RESPONSE

    prop = response['context']['properties']
    for p in prop:
        p['namespace'] = alexa_interface
        p['name'] = directive
        p['timeOfSample'] = get_utc_timestamp()
    response['event']['header']['messageId'] = get_uuid()
    response['event']['header']['correlationToken'] = request['directive']['header']['correlationToken']
    response['event']['endpoint'] = request['directive']['endpoint']

    return response

discover = {
  "directive": {
    "header": {
      "namespace": "Alexa.Discovery",
      "name": "Discover",
      "payloadVersion": "3",
      "messageId": "1bd5d003-31b9-476f-ad03-71d471922820"
    },
    "payload": {
      "scope": {
        "type": "BearerToken",
        "token": "access-token-from-skill"
      }
    }
  }
}
response = lambda_handler(discover, "")

turnon = {
  "directive": {
    "header": {
      "namespace": "Alexa.PowerController",
      "name": "TurnOff",
      "payloadVersion": "3",
      "messageId": "1bd5d003-31b9-476f-ad03-71d471922820",
      "correlationToken": "dFMb0z+PgpgdDmluhJ1LddFvSqZ/jCc8ptlAKulUj90jSqg=="
    },
    "endpoint": {
      "scope": {
        "type": "BearerToken",
        "token": "access-token-from-skill"
      },
      "endpointId": "Blu-ray",
      "cookie": {}
    },
    "payload": {}
  }
}
response = lambda_handler(turnon, "")