#!/usr/bin/python
#
# Copyright 2013 Google Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Command Line interface for python interop tests

Called using a JSON argument that contains all parameters.

Operations each contain a test and generate function that will be called
when an operation is specified in the JSON

There is also a create function that will create keys with the flags passed
in.

@author: dlundberg@google.com (Devin Lundberg)
"""
import json
import os
import sys

# makes interop_test think it's part of the keyczar package
test_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(os.path.dirname(test_dir))
sys.path.insert(1, os.path.join(parent_dir, "src/keyczar/"))
import keyczar
__package__ = "keyczar"

import errors
import keyczar
import keyczart
import readers
import util


class BaseOperation(object):
  """
  Base class for all operations. An operation is a basic
  functionality of keyczar such as encrypting or signing
  that can be verified. All operations have a Generate
  function that will generate output that can tested using
  the Test function.
  """

  def __init__(self, key_path, test_data):
    """
    Sets test_data and key_path for the operation
    """
    self.test_data = str(test_data)
    self.key_path = key_path

  def Generate(self, algorithm, options):
    """
    Generates output that will later be saved to file and
    tested using the test function in this and other
    implementations of keyczar. An example is the encrypt
    operation which will return the ciphertext.

    @param algorithm: name of key algorithm with size
    @type algorithm: string

    @param *options: additional parameters listed in children

    @raise NotImplementedError: If the child class does not implement
    """
    raise NotImplementedError()

  def Test(self, output, algorithm, chosen_params, test_options):
    """
    Will return without error if the input is valid.

    Will verify that the data generated by Generate
    works correctly. For example in the case of the encrypt
    operation this will decrypt the ciphertext and check if
    the value is equal to what was input as plaintext.

    @param output: the output from the Generate function
    @type output: string

    @param algorithm: name of key algorithm with size
    @type algorithm: string

    @param chosen_params: list of option names chosen for generate
    @type chosen_params: list of strings

    @param *test_options: additional parameters listed in children

    @raise AssertionError: If the test fails
    @raise NotImplementedError: If the child class does not implement
    """
    raise NotImplementedError()

  def _GetKeyPath(self, algorithm):
    return os.path.join(self.key_path, algorithm)

  def _GetReader(self, algorithm, crypter, pub_key):
    """ Gets a keyczar reader for the given algorithm and key set crypter"""
    reader = readers.CreateReader(
        self._GetKeyPath(algorithm + crypter + pub_key))
    if crypter:
      key_set_crypter = keyczar.Crypter.Read(self._GetKeyPath(crypter))
      reader = readers.EncryptedReader(reader, key_set_crypter)
    return reader


def JsonOutput(method):
  """ Decorator for the generate function to put output in json form. """
  def WrapperFunction(*args):
    output = method(*args)
    b64_output = util.Base64WSEncode(output)
    return json.dumps({"output": b64_output})
  return WrapperFunction


def JsonInput(method):
  """ Decorator for the test function to read input in json form. """
  def WrapperFunction(self, json_dict, *args):
    output = util.Base64WSDecode(json_dict["output"])
    return method(self, output, *args)
  return WrapperFunction


class UnversionedSignOperation(BaseOperation):
  """ Operation that tests unversioned signing. """

  def __init__(self, *args):
    super(UnversionedSignOperation, self).__init__(*args)

  @JsonOutput
  def Generate(self, algorithm, options):
    if options["encoding"] == "encoded":
      encoder = util.Base64WSEncode
    else:
      encoder = None
    signer = keyczar.UnversionedSigner(self._GetReader(
        algorithm, options["cryptedKeySet"], options.get("pubKey", "")))
    signature = signer.Sign(self.test_data, encoder)
    return signature

  @JsonInput
  def Test(self, signature, algorithm, generate_options, test_options):
    if generate_options["encoding"] == "encoded":
      decoder = util.Base64WSDecode
    else:
      decoder = None
    verifier_class = (keyczar.UnversionedVerifier
                      if test_options["class"] == "verifier"
                      else keyczar.UnversionedSigner)
    verifier = verifier_class(self._GetReader(
        algorithm, generate_options["cryptedKeySet"],
        test_options.get("pubKey", "")))
    assert verifier.Verify(self.test_data, signature, decoder=decoder)


class SignedSessionOperation(BaseOperation):
  """ Operation that tests Signed Sessions """

  def __init__(self, *args):
    super(SignedSessionOperation, self).__init__(*args)

  def Generate(self, crypter_algorithm, options):
    signer = keyczar.Signer(
        self._GetReader(options["signer"], options["cryptedKeySet"], ""))
    key_encrypter = keyczar.Encrypter(self._GetReader(
        crypter_algorithm, options["cryptedKeySet"],
        options.get("pubKey", "")))
    crypter = keyczar.SignedSessionEncrypter(key_encrypter, signer)
    encrypted_data = crypter.Encrypt(self.test_data)

    # save session material to a seperate file
    session_material = crypter.session_material
    output = {
        "output": encrypted_data,
        "sessionMaterial": session_material
    }

    return json.dumps(output)

  def Test(self, output, algorithm, generate_options, test_options):
    encrypted_data = output["output"]
    session_material = output["sessionMaterial"]
    signer_algorithm = generate_options["signer"]
    verifier = keyczar.Verifier(self._GetReader(
        signer_algorithm, generate_options["cryptedKeySet"], ""))
    key_crypter = keyczar.Crypter(self._GetReader(
        algorithm, generate_options["cryptedKeySet"],
        test_options.get("pubKey", "")))
    session_decrypter = keyczar.SignedSessionDecrypter(
        key_crypter, verifier, session_material)
    decrypted_data = session_decrypter.Decrypt(encrypted_data)
    assert decrypted_data == self.test_data


class AttachedSignOperation(BaseOperation):
  """ Operation that tests attached signing. """

  def __init__(self, *args):
    super(AttachedSignOperation, self).__init__(*args)

  @JsonOutput
  def Generate(self, algorithm, options):
    if options["encoding"] == "encoded":
      encoder = util.Base64WSEncode
    else:
      encoder = None
    signer = keyczar.Signer(self._GetReader(
        algorithm, options["cryptedKeySet"], options.get("pubKey", "")))
    signature = signer.AttachedSign(self.test_data, "", encoder)
    return signature

  @JsonInput
  def Test(self, signature, algorithm, generate_options, test_options):
    if generate_options["encoding"] == "encoded":
      decoder = util.Base64WSDecode
    else:
      decoder = None
    verifier_class = (keyczar.Verifier
                      if test_options["class"] == "verifier"
                      else keyczar.Signer)
    verifier = verifier_class(self._GetReader(
        algorithm, generate_options["cryptedKeySet"],
        test_options.get("pubKey", "")))
    assert verifier.AttachedVerify(signature, "", decoder=decoder)


class SignOperation(BaseOperation):
  """ Operation that tests signing. """

  def __init__(self, *args):
    super(SignOperation, self).__init__(*args)

  @JsonOutput
  def Generate(self, algorithm, options):
    if options["encoding"] == "encoded":
      encoder = util.Base64WSEncode
    else:
      encoder = None
    signer = keyczar.Signer(self._GetReader(
        algorithm, options["cryptedKeySet"], options.get("pubKey", "")))
    signature = signer.Sign(self.test_data, encoder)
    return signature

  @JsonInput
  def Test(self, signature, algorithm, generate_options, test_options):
    if generate_options["encoding"] == "encoded":
      decoder = util.Base64WSDecode
    else:
      decoder = None
    verifier_class = (keyczar.Verifier
                      if test_options["class"] == "verifier"
                      else keyczar.Signer)
    verifier = verifier_class(self._GetReader(
        algorithm, generate_options["cryptedKeySet"],
        test_options.get("pubKey", "")))
    assert verifier.Verify(self.test_data, signature, decoder=decoder)


class EncryptOperation(BaseOperation):
  """ Operation that tests encryption. """

  def __init__(self, *args):
    super(EncryptOperation, self).__init__(*args)

  @JsonOutput
  def Generate(self, algorithm, options):
    if options["encoding"] == "encoded":
      encoder = util.Base64WSEncode
    else:
      encoder = None
    if options["class"] == "crypter":
      crypter_class = keyczar.Crypter
    else:
      crypter_class = keyczar.Encrypter
    crypter = crypter_class(self._GetReader(
        algorithm, options["cryptedKeySet"], options.get("pubKey", "")))
    ciphertext = crypter.Encrypt(self.test_data, encoder)
    return ciphertext

  @JsonInput
  def Test(self, ciphertext, algorithm, generate_options, test_options):
    if generate_options["encoding"] == "encoded":
      decoder = util.Base64WSDecode
    else:
      decoder = None
    crypter = keyczar.Crypter(self._GetReader(
        algorithm, generate_options["cryptedKeySet"],
        test_options.get("pubKey", "")))
    assert crypter.Decrypt(ciphertext, decoder=decoder) == self.test_data

operations = {
    "unversioned": UnversionedSignOperation,
    "attached": AttachedSignOperation,
    "sign": SignOperation,
    "encrypt": EncryptOperation,
    "signedSession": SignedSessionOperation
}

CREATE = "create"
GENERATE = "generate"
TEST = "test"


def Create(keyczart_commands):
  for command in keyczart_commands:
    keyczart.main(command)


def Generate(
    operation_name, key_path, algorithm, generate_options, test_data):
  operation = operations[operation_name]
  current_operation = operation(key_path, test_data)
  output = current_operation.Generate(algorithm, generate_options)
  print output


def Test(operation_name, output, key_path,
         algorithm, generate_options, test_options, test_data):
  operation = operations[operation_name]
  current_operation = operation(key_path, test_data)
  current_operation.Test(output, algorithm, generate_options, test_options)
  print "Test passes"


def Usage():
  raise errors.KeyczarError(
      "Interop tests take a single JSON string describing the parameters\n\n"
      "The format of the JSON is as follows:\n"
      "{\n"
      "  \"command\" : (\"create\"|\"generate\"|\"test\"),\n"
      "  ... any command specific arguments ...\n"
      "}\n"
      )


def main(argv):
  if len(argv) > 2 or not argv:
    Usage()
  else:
    args = json.loads(argv[0])
    if "command" not in args:
      Usage()
    else:
      cmd = args["command"]
      if cmd == CREATE:
        Create(args["keyczartCommands"])
      elif cmd == GENERATE:
        Generate(args["operation"], args["keyPath"],
                 args["algorithm"], args["generateOptions"], args["testData"])
      elif cmd == TEST:
        Test(args["operation"], args["output"], args["keyPath"],
             args["algorithm"], args["generateOptions"],
             args["testOptions"], args["testData"])
      else:
        Usage()

if __name__ == "__main__":
  sys.exit(main(sys.argv[1:]))  # sys.argv[0] is name of program
