"""
_________________________________
SERVER_NAME
Server description goes here
__________________________________
"""

import flask
import flask_cors
import pymongo
import os
import dotenv
import random_utilities
import schedule
import email
import re
import imaplib

from models.response import Response
from models.time_created import TimeCreatedModel


"""
__________________________________
DEVELOPMENTAL ENVIRONMENT VARIABLES
__________________________________
"""
if os.environ.get("environment") != "production":
    dotenv.load_dotenv()


"""
__________________________________
SERVER INSTANCE SETUP
__________________________________
"""
server_instance = flask.Flask(
    __name__, static_folder="./assets/", static_url_path="/server_name/assets/"
)
flask_cors.CORS(server_instance, resources={r"*": {"origins": "*"}})


"""
__________________________________
DATABASE CONNECTION
__________________________________
"""
IS_DB_CONNECTED = False
if os.environ.get(
    "MONGODB_HOST"
    if os.environ.get("ENVIRONMENT") == "production"
    else "MONGODB_DEV_HOST"
):
    farmhouse, IS_DB_CONNECTED = random_utilities.initiate_mongodb_connection(
        mongo_host=os.environ.get(
            "MONGODB_HOST"
            if os.environ.get("ENVIRONMENT") == "production"
            else "MONGODB_DEV_HOST"
        ),
        database_name=os.environ.get("DATABASE_NAME"),
        collection_name="farmhouse",
    )
else:
    IS_DB_CONNECTED = False
    random_utilities.log("No database connection specified. Not connecting to any.")


"""
____________________________________________________
EMAIL CAPABILITIES: USED TO RECEIVE CONTRACT REVIEWS
____________________________________________________
"""
_from = "support@towardscommonfoundry.com"
_to = "lebyane.lm@gmail.com"
IMAP_SERVER = "imap.secureserver.net"
IMAP_SERVER_PORT = 993

mail = imaplib.IMAP4_SSL(host=IMAP_SERVER, port=IMAP_SERVER_PORT)
mail.login(os.environ["EMAIL_ADDRESS_USERNAME"], os.environ["EMAIL_ADDRESS_KEY"])

mail.select("Inbox")


# Every 5 seconds check the mail to find review responses of contracts
def check_email_review_responses():
    random_utilities.log("Refreshing mail review search.")
    # Contract review emails will be sent to reviewers and replies made on the email will be taken as review response.
    status, data = mail.search(None, "(ALL)")
    mail_ids = []
    reviewers_ids = ["Libby Lebyane <lebyane.lm@gmail.com>"]

    for block in data:
        mail_ids += block.split()

    for i in mail_ids:
        status, data = mail.fetch(i, "(RFC822)")
        if status == "OK":
            for response_part in data:
                if isinstance(response_part, tuple):
                    message = email.message_from_bytes(response_part[1])

                    # Only allow reviews only from selected reviewers
                    random_utilities.log(
                        [
                            message["subject"],
                            "Reviewal:" in message["subject"],
                            message["from"] in reviewers_ids,
                        ]
                    )
                    if (
                        message["from"] in reviewers_ids
                        and "Reviewal:" in message["subject"]
                    ):
                        # Extraxt the contract ID from the subject line.
                        contract_id = message["subject"].split("Reviewal: ")
                        if len(contract_id) == 2:
                            contract_id = contract_id[1]
                            random_utilities.log(["ContractID: ", contract_id])
                        else:
                            # MOVE THE MAIL TO "MALFORMED MAILS" FOLDER AND DELETE IT FROM THE INBOX
                            mail.copy(i, "Malformed")
                            mail.store(i, "+FLAGS", "\\Deleted")
                            mail.expunge()
                            return print(
                                f"Mail {message['subject']} moved to the malformed folder."
                            )
                        contract = (
                            random_utilities.default_database.farmhouse_drafts.find_one(
                                dict(key=contract_id)
                            )
                        )
                        print("Contract name:", contract.get("name"))

                        mail_content = ""
                        if message.is_multipart():
                            for part in message.get_payload():
                                if part.get_content_type() == "text/plain":
                                    mail_content += part.get_payload()
                        else:
                            mail_content = message.get_payload()

                        if contract:
                            if mail_content.strip().lower() in (
                                "approved",
                                "approved.",
                            ):
                                contract["approval_date"] = TimeCreatedModel().__dict__
                                contract["is_approved"] = True

                                # MOVE THE CONTRACT FROM DRAFTS TO THE LIVE CONTRACT LINE.
                                insert_return = random_utilities.default_database.farmhouse.insert_one(
                                    contract
                                )
                                random_utilities.log(
                                    f"Contract approval movement: ", insert_return
                                )

                                if insert_return:
                                    # DELETE THE DRAFT CONTRACT FROM THE DRAFTS RECORD.
                                    delete_return = random_utilities.default_database.farmhouse_drafts.delete_one(
                                        dict(key=contract_id)
                                    )
                                    print(
                                        f"Contract [{contract_id}] has been approved. And draft removed: {delete_return}"
                                    )

                                    # UPDATE THE CONTRACTS OWNED TO THE CURATORS
                                    main_curator = contract["curator"]
                                    other_curators = contract["other_curators"]
                                    curators = [main_curator, *other_curators]
                                    for curator_index, curator in enumerate(curators):
                                        curator = random_utilities.default_database.accounts.find_one(
                                            dict(email_address=curator)
                                        )

                                        if curator:
                                            if curator_index == 0:
                                                curator["contracts"].append(contract_id)
                                            else:
                                                if (
                                                    curator["contracts_featured"]
                                                    is None
                                                ):
                                                    curator["contracts_featured"] = []
                                                curator["contracts_featured"].append(
                                                    contract_id
                                                )

                                            # UPDATE THE FINAL CHANGES.
                                            del curator["_id"]
                                            random_utilities.default_database.accounts.update_one(
                                                dict(email_address=curator),
                                                {"$set": curator},
                                            )

                                    # TODO: Send an email to each curator.

                                    # MOVE MAIL TO "REVIEWED" FOLDER
                                    mail.copy(i, "Reviewed")
                                    mail.store(i, "+FLAGS", "\\Deleted")
                                    mail.expunge()
                                    print(
                                        f"Mail {message['subject']} moved to the reviewed folder."
                                    )
                                else:
                                    return report_non_delivery(insert_return)
                            else:
                                stages = {
                                    "0": "basic",
                                    "1": "story",
                                    "2": "finances",
                                    "3": "rewards",
                                    "4": "curators",
                                    "5": "milestones",
                                    "6": "documentation",
                                }

                                # REGULAR EXPRESSION FOR DETERMINING REVIEW ITEMS.
                                review_item_regex = (
                                    r"([a-zA-Z]{2})\((\d+)\)(?:\((\d+)\))?: (.*?\.)"
                                )

                                # FIND ALL THE REVIEW ITEMS IN THE MAIL CONTENT.
                                review_items = re.findall(
                                    review_item_regex, mail_content
                                )
                                print('Review items:', review_items)

                                # GO THROUGH EACH REVIEW ITEM AND PROCESS THE REVIEW.
                                has_unsaved_updates = False
                                for review_item in review_items:
                                    # FORMAT: (type, stage, optional index, message).
                                    # CHECK THE STAGE OF THE REVIEW DOCUMENTS.
                                    if review_item[1] in ("0", "1", "2"):
                                        # INSERT THE REVIEW MESSAGE.
                                        contract["draft_progress"][
                                            stages[review_item[1]]
                                        ]["review_items"][review_item[0]] = review_item[
                                            3
                                        ]

                                        # SET THE UNREAD FLAG
                                        contract["draft_progress"][
                                            stages[review_item[1]]
                                        ]["has_unread_reviews"] = True
                                        has_unsaved_updates = True
                                    else:
                                        # DIRECTOR DOCUMENTS USE A DIFFERENT REVIEW FORMAT.
                                        if review_item[2] != "":
                                            if review_item[0] == "dd":
                                                # INSERT THE REVIEW MESSAGE.
                                                contract["draft_progress"][
                                                    stages[review_item[1]]
                                                ]["review_items"][review_item[0]][
                                                    review_item[2]
                                                ] = review_item[
                                                    3
                                                ]
                                            else:
                                                contract["draft_progress"][
                                                    stages[review_item[1]]
                                                ]["review_items"][
                                                    review_item[2]
                                                ] = review_item[
                                                    3
                                                ]
                                            contract["draft_progress"][
                                                stages[review_item[1]]
                                            ]["has_unread_reviews"] = True
                                            has_unsaved_updates = True
                                        else:
                                            if review_item[1] == "6":
                                                # INSERT THE REVIEW MESSAGE.
                                                contract["draft_progress"][
                                                    stages[review_item[1]]
                                                ]["review_items"][
                                                    review_item[0]
                                                ] = review_item[
                                                    3
                                                ]

                                                # SET THE UNREAD FLAG
                                                contract["draft_progress"][
                                                    stages[review_item[1]]
                                                ]["has_unread_reviews"] = True
                                                has_unsaved_updates = True

                                """
                                REFERENCE.
                                Basics (0)
                                ct(0): Contract title
                                cb(0): Contract brief
                                fp(0): Funding purpose
                                pc(0): Primary category
                                sc(0): Secondary category
                                co(0): Country of origin
                                po(0): Province of origin
                                cl(0): Contractor logo
                                pv(0): Presentation video

                                Story (1)
                                st(1): Story

                                Finances (2)
                                hg(2): Hetching goal
                                cp(2): Contract period

                                Rewards (3)
                                re(3)(<index>): Reward at an index

                                Curators (4)
                                cu(4)(<index>): Curator at an index 

                                Milestones (5)
                                mi(5)(<index>): Milestone at an index


                                Company documentations (6)
                                cr(5): Company registration
                                pa(5): Proof of address
                                pb(5): Proof of bank
                                tc(5): Tax certificate
                                
                                Director documentation (6)
                                dd(6)(<index>): Director at an index
                                """

                                # UPDATE THE DATABASE RECORDS FOR THE CONTRACT
                                print("Has unsaved changes?:", has_unsaved_updates)
                                if has_unsaved_updates:
                                    del contract["_id"]
                                    random_utilities.default_database.farmhouse_drafts.update_one(
                                        (dict(key=contract_id)), {"$set": contract}
                                    )

                                    # MOVE MAIL TO "GENERAL" FOLDER
                                    mail.copy(i, "Reviewed")
                                    mail.store(i, "+FLAGS", "\\Deleted")
                                    mail.expunge()
                                    print(
                                        f"Mail {message['subject']} moved to the reviewed folder."
                                    )
                                else:
                                    # MOVE MAIL TO "GENERAL" FOLDER
                                    mail.copy(i, "Malformed")
                                    mail.store(i, "+FLAGS", "\\Deleted")
                                    mail.expunge()
                                    print(
                                        f"Mail {message['subject']} moved to the malformed folder."
                                    )
                        else:
                            return report_non_delivery(
                                " with review: ".join([contract_id, mail_content])
                            )
                    else:
                        # MOVE MAIL TO "GENERAL" FOLDER
                        mail.copy(i, "General")
                        mail.store(i, "+FLAGS", "\\Deleted")
                        mail.expunge()
        else:
            pass


def report_non_delivery(item):
    random_utilities.log(item)
    # Todo: Send an email to let the review know review was not recieved


schedule.every(1).seconds.do(check_email_review_responses)


while True:
    schedule.run_pending()
