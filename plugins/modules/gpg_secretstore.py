#!/usr/bin/python3
# coding: utf-8

# (c) 2021, Famedly GmbH
# GNU Affero General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/agpl-3.0.txt)

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import hashlib
import traceback

ANSIBLE_METADATA = {
    "metadata_version": "1.1",
    "status": ["preview"],
    "supported_by": "community",
}

DOCUMENTATION = r"""
---
module: gpg_secretstore
author:
    - Jadyn Emma Jäger (@jadyndev)
    - Lars Kaiser (@lrsksr)
requirements:
    - PyYAML >= 6.0
    - filelock >= 3.0.12
    - python >= 3.7
    - python-gnupg >= 0.4.8
short_description: Save and retrieve secrets from pass compatible files
description:
    - Save and retrieve secrets from pass compatible files. Secrets can be random strings or be generated by a command.
      Secrets in yaml or json format can be parsed as such and will return complex data
options:
    password_store_path:
        description:
            - The path in which the password database is stored
        required: False
        type: str
        default: ~/.password-store/
    file_extension:
        description:
            - File extension for the encrypted files
        required: False
        type: str
        default: .gpg
    keyring:
        description:
            - Keyring containing all recipients public keys, and the private key(s) for decryption
        required: False
        type: str
        default: pubring.kbx
    gnupg_home:
        description:
            - Folder containing the Keyring and other gnupg config files
        required: False
        type: str
        default: ~/.gnupg
    pass_gpg_id_file:
        description:
            - Filename of the file containing the recipient pub key IDs
        required: False
        type: str
        default: .gpg-id
    state:
        description:
            - Whether the password file should exist
        required: True
        type: str
        choices: 'present', 'absent'
        default: 'present'
    password_slug
        description:
            - Password slug, something like `servers/prod/some_secret`
              used to look find the encrypted files, compatible with the unix pass utility
        required: True
        type: str
    data_type:
        description:
            - Datatype of the encrypted data. If not `plain` the encrypted file will be parsed.
              Throws an exception if it can't be parsed
        required: False
        type: str
        choices: 'plain', 'yaml', 'json'
        default: 'plain'
    overwrite:
        description:
            - Forces the regeneration of a secret
        required: False
        type: bool
        default: False
    secret_type:
        description:
            - How a new secret has to be generated
        required: False
        type: str
        choices: 'random', 'binary', 'user_supplied'
        default: 'random'
    secret_binary:
        description:
            - If `secret_type` is binary, the supplied command is executed and STDOUT is used as the secret.
            - If the binary generates yaml or json, set the `data_type` accordingly
        required=False
        type: str
    secret_length:
        description:
            - If `secret_type` is random, this defines how many characters the new secret has.
        required: False
        type: int
        default: 20
    secret_pattern:
        description:
            - If `secret_type` is random, this defines the characters used in the random string with regex
              You may just leave it as is
        required: False
        type: str
        default: "([A-Za-z0-9])"
    user_supplied_secret:
        description:
            - If `secret_type` is user_supplied, this value defines the secret
        required: False
        type: str
"""

EXAMPLES = r"""
- name: Generate password, if not exists
  gpg_secretstore:
    password_slug: 'example/secret'
  delegate_to: localhost

- name: Generate password, everytime
  gpg_secretstore:
    password_slug: 'example/overwrite'
    overwrite: true
  delegate_to: localhost

- name: Generate password with binary
  gpg_secretstore:
    password_slug: 'example/bin'
    secret_type: 'binary'
    secret_binary: 'ip a'
  delegate_to: localhost

- name: Read json secret
  gpg_secretstore:
    password_slug: 'example/json'
    data_type: 'json'
  delegate_to: localhost

- name: Read yaml secret
  gpg_secretstore:
    password_slug: 'example/yaml'
    data_type: 'yaml'
  delegate_to: localhost
  register: yaml
"""

RETURN = r"""
secret:
    description: Decrypted Secret, either loaded from the database (if OK) or newly generated (if CHANGED)
    type: str / list / dict
action:
    description: Gives information on what the operation:
                 add:    Secret was __not__ found in the database and is therefore generated and added
                 update: Secret was found in the database and was updated
    type: str
    choices: add / update
password_slug:
    description: Returns the password slug
    type: str
diff:
    description: List of the old and current gpg recipients key-ids
    type: diff
message:
    description: Human-readable information about the (completed) task
    type: str
warning:
    description: Human-readable warnings that accrued during the task
    type: str
"""

from ansible.module_utils.basic import AnsibleModule, missing_required_lib
from ansible_collections.famedly.base.plugins.module_utils.gpg_utils import *
from ansible.utils.display import Display

LIB_IMP_ERR = None
try:
    from filelock import FileLock
    import gnupg

    HAS_LIB = True
except ImportError:
    HAS_LIB = False
    LIB_IMP_ERR = traceback.format_exc()

logging = Display()


class SecretGenerator:
    ALLOWED_SECRET_TYPES = ["random", "binary", "user_supplied"]

    def __init__(self, secret_type: str = "random", **kwargs):
        self.secret_type = secret_type.lower()
        self.kwargs = kwargs
        if self.secret_type not in self.ALLOWED_SECRET_TYPES:
            raise NotImplementedError(
                "Secret type {} is not supported".format(secret_type)
            )

    def getSecret(self):
        if self.secret_type == "random":
            return self.__randomSecret(**self.kwargs)
        if self.secret_type == "binary":
            return self.__binarySecret(**self.kwargs)
        if self.secret_type == "user_supplied":
            return self.__userSuppliedSecret(**self.kwargs)
        raise NotImplementedError(
            "Secret type {} is not supported".format(self.secret_type)
        )

    @staticmethod
    def __randomSecret(
        length: int = 30, letter_pattern: str = "([A-Za-z0-9])", **kwargs
    ):
        import secrets
        import re
        import string

        characters = re.findall(letter_pattern, string.printable)
        return "".join(secrets.choice(characters) for i in range(length))

    @staticmethod
    def __binarySecret(binary: str, **kwargs):
        import subprocess

        binary = binary.split()
        process = subprocess.run(binary, capture_output=True, check=True)
        return process.stdout.decode("UTF-8")

    @staticmethod
    def __userSuppliedSecret(user_supplied_secret: str, **kwargs):
        return user_supplied_secret


def main():
    module = AnsibleModule(
        argument_spec=dict(
            # General arguments
            password_store_path=dict(
                required=False, type="str", default="~/.password-store/", no_log=False
            ),
            file_extension=dict(required=False, type="str", default=".gpg"),
            keyring=dict(required=False, type="str", default="pubring.kbx"),
            gnupg_home=dict(required=False, type="str", default="~/.gnupg"),
            pass_gpg_id_file=dict(
                required=False, type="str", default=".gpg-id", no_log=False
            ),
            # Password specific arguments
            state=dict(
                required=False,
                type="str",
                choices=["present", "absent"],
                default="present",
            ),
            password_slug=dict(required=True, type="str", no_log=False),
            data_type=dict(
                required=False,
                type="str",
                choices=["plain", "yaml", "json"],
                default="plain",
            ),
            # Password generation arguments
            overwrite=dict(required=False, type="bool", default="false"),
            secret_type=dict(
                required=False,
                type="str",
                choices=["random", "binary", "user_supplied"],
                default="random",
            ),
            secret_binary=dict(required=False, type="str"),
            secret_length=dict(required=False, type="int", default=20),
            secret_pattern=dict(required=False, type="str", default="([A-Za-z0-9])"),
            user_supplied_secret=dict(required=False, type="str", no_log=True),
        ),
        supports_check_mode=True,
    )

    # Check if gnupg is present
    if not HAS_LIB:
        module.fail_json(
            msg=missing_required_lib("python-gnupg"), exception=LIB_IMP_ERR
        )

    store = SecretStore(
        password_store_path=module.params["password_store_path"],
        file_extension=module.params["file_extension"],
        keyring=module.params["keyring"],
        gnupg_home=module.params["gnupg_home"],
        pass_gpg_id_file=module.params["pass_gpg_id_file"],
    )

    secretGenerator = SecretGenerator(
        secret_type=module.params["secret_type"],
        data_type=module.params["data_type"],
        binary=module.params["secret_binary"],
        length=module.params["secret_length"],
        letter_pattern=module.params["secret_pattern"],
        user_supplied_secret=module.params["user_supplied_secret"],
    )

    state = module.params["state"]
    password_slug = module.params["password_slug"]
    data_type = module.params["data_type"]
    overwrite = module.params["overwrite"]

    result = dict(
        changed=False,
        message="",
        warning="",
        password_slug=module.params["password_slug"],
        secret="",
        diff={
            "before_header": "{} gpg recipients".format(password_slug),
            "after_header": "{} gpg recipients".format(password_slug),
            "before": [],
            "after": [],
        },
    )

    failed = False

    lock = FileLock(
        (Path("/tmp/") / hashlib.md5(password_slug.encode()).hexdigest()).as_posix()
    )
    with lock:
        if state == "present":
            try:
                result["diff"]["before"] = store.get_recipients_from_encrypted_file(
                    slug=password_slug
                )
                if not overwrite:
                    result["secret"] = store.get(
                        slug=password_slug, data_type=data_type
                    )
                    result["changed"] = False
                else:
                    result[
                        "message"
                    ] = "Secret rotation requested: rotating, if possible."
                    result["secret"] = secretGenerator.getSecret()
                    result["action"] = "update"
                    result["changed"] = True
                result["diff"]["after"] = result["diff"]["before"]

            except FileNotFoundError:
                result["message"] = "Secret not found! Generation new secret"
                result["secret"] = secretGenerator.getSecret()
                result["diff"]["before"] = []
                result["diff"]["after"] = store.get_recipients(slug=password_slug)
                result["action"] = "add"
                result["changed"] = True

            except RecipientsMismatchError:
                result["warning"] = "Secret-Recipient-Mismatch! Re-encrypting."
                result["secret"] = store.get(
                    slug=password_slug, data_type=data_type, check_recipients=False
                )
                result["diff"]["before"] = store.get_recipients_from_encrypted_file(
                    slug=password_slug
                )
                result["diff"]["after"] = store.get_recipients(slug=password_slug)
                result["action"] = "update"
                result["changed"] = True

            if result["changed"] and not module.check_mode:
                store.put(
                    slug=password_slug, data=result["secret"], data_type=data_type
                )
                result["diff"]["after"] = store.get_recipients_from_encrypted_file(
                    slug=password_slug
                )

        if state == "absent":
            try:
                if module.check_mode:
                    store.get(slug=password_slug, data_type=data_type)
                else:
                    store.remove(slug=password_slug)
                result["message"] = "Secret will be deleted!"
                result["diff"]["before"] = store.get_recipients_from_encrypted_file(
                    slug=password_slug
                )
                result["diff"]["after"] = []
                result["action"] = "remove"
                result["changed"] = True

            except FileNotFoundError:
                result["message"] = "Secret didn't exist"
                result["diff"]["before"] = []
                result["diff"]["after"] = []
                result["changed"] = False

    if result["message"]:
        module.log(result["message"])

    if result["warning"]:
        module.warn(result["warning"])

    result["diff"]["before"] = "\n".join(result["diff"]["before"]) + "\n"
    result["diff"]["after"] = "\n".join(result["diff"]["after"]) + "\n"

    if failed:
        module.fail_json(**result)
    else:
        module.exit_json(**result)


if __name__ == "__main__":
    main()
