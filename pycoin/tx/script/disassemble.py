import binascii

from pycoin.ecdsa import generator_secp256k1, possible_public_pairs_for_signature
from pycoin.encoding import (public_pair_to_bitcoin_address, hash160_sec_to_bitcoin_address,
                             sec_to_public_pair, is_sec_compressed)

from pycoin.serialize import b2h
from pycoin.tx.script.VM import VM
from pycoin.tx.script.SolutionChecker import SolutionChecker

from pycoin.tx.script.checksigops import parse_signature_blob
from pycoin.tx import SIGHASH_ALL, SIGHASH_NONE, SIGHASH_SINGLE, SIGHASH_ANYONECANPAY


def sighash_type_to_string(sighash_type):
    anyonecanpay = sighash_type & SIGHASH_ANYONECANPAY
    sighash_type &= ~SIGHASH_ANYONECANPAY
    if sighash_type == SIGHASH_ALL:
        sighash_str = 'SIGHASH_ALL'
    elif sighash_type == SIGHASH_NONE:
        sighash_str = 'SIGHASH_NONE'
    elif sighash_type == SIGHASH_SINGLE:
        sighash_str = 'SIGHASH_SINGLE'
    else:
        sighash_str = 'SIGHASH_UNKNOWN'
    if anyonecanpay:
        sighash_str += ' | SIGHASH_ANYONECANPAY'
    return sighash_str


def add_signature_annotations(annotations, signature_blob, signature_for_hash_type_f, output_script):
    sig_pair, sig_type = parse_signature_blob(signature_blob)
    annotations.append("r: {0:#066x}".format(sig_pair[0]))
    annotations.append("s: {0:#066x}".format(sig_pair[1]))
    sig_hash = signature_for_hash_type_f(sig_type, output_script)
    annotations.append("z: {0:#066x}".format(sig_hash))
    annotations.append("signature type %s" % sighash_type_to_string(sig_type))
    addresses = []
    pairs = possible_public_pairs_for_signature(generator_secp256k1, sig_hash, sig_pair)
    for pair in pairs:
        for comp in (True, False):
            address = public_pair_to_bitcoin_address(pair, compressed=comp, address_prefix=b'\0')
            addresses.append(address)
    annotations.append(" sig for %s" % " ".join(addresses))


def add_address_annotations(annotations, hash160_blob, address_prefix):
    address = hash160_sec_to_bitcoin_address(hash160_blob, address_prefix=address_prefix)
    annotations.append("%s... corresponds to %s" % (b2h(hash160_blob)[:12], address))


def add_sec_annotations(a1, data, address_prefix):
    pair = sec_to_public_pair(data)
    is_compressed = is_sec_compressed(data)
    a1.append("SEC for %scompressed %s" % (
            "" if is_compressed else "un", public_pair_to_bitcoin_address(
                pair, compressed=is_compressed, address_prefix=address_prefix)))


def instruction_for_opcode(opcode, data):
    if data is None or len(data) == 0:
        return VM.INT_TO_OPCODE.get(opcode, "(UNKNOWN OPCODE)")
    return "[PUSH_%d] %s" % (opcode, binascii.hexlify(data))


def annotation_f_for_scripts(input_script, output_script, signature_for_hash_type_f):
    is_p2sh = SolutionChecker.is_pay_to_script_hash(output_script)
    in_ap = b'\0'
    out_ap = b'\0'
    if is_p2sh:
        out_ap = b'\5'

    def input_annotations_f(pc, opcode, data):
        a0, a1 = [], []
        if pc == 0:
            a0.append("--- SIGNATURE SCRIPT START")
        ld = len(data) if data is not None else 0
        if ld in (71, 72) and not is_p2sh:
            add_signature_annotations(a1, data, signature_for_hash_type_f, output_script)
        if ld == 20:
            add_address_annotations(a1, data, address_prefix=in_ap)
        if ld in (33, 65):
            add_sec_annotations(a1, data, address_prefix=in_ap)
        return a0, a1

    def output_annotations_f(pc, opcode, data):
        a0, a1 = [], []
        if pc == 0:
            a0.append("--- PUBLIC KEY SCRIPT START")
        ld = len(data) if data is not None else 0
        if ld == 20:
            add_address_annotations(a1, data, address_prefix=out_ap)
        if ld in (33, 65):
            add_sec_annotations(a1, data, address_prefix=out_ap)
        return a0, a1

    return input_annotations_f, output_annotations_f


def disassemble_scripts(input_script, output_script, lock_time, signature_for_hash_type_f):
    "yield pre_annotations, pc, opcode, instruction, post_annotations"

    input_annotations_f, output_annotations_f = annotation_f_for_scripts(
        input_script, output_script, signature_for_hash_type_f)
    pc = 0
    while pc < len(input_script):
        opcode, data, new_pc = VM.DataCodec.get_opcode(input_script, pc)
        pre_annotations, post_annotations = input_annotations_f(pc, opcode, data)
        yield pre_annotations, pc, opcode, instruction_for_opcode(opcode, data), post_annotations
        pc = new_pc

    pc = 0
    while pc < len(output_script):
        opcode, data, new_pc = VM.DataCodec.get_opcode(output_script, pc)
        pre_annotations, post_annotations = output_annotations_f(pc, opcode, data)
        yield pre_annotations, pc, opcode, instruction_for_opcode(opcode, data), post_annotations
        pc = new_pc

    if not SolutionChecker.is_pay_to_script_hash(output_script):
        return

    stack = []
    # ## BRAIN DAMAGE
    sc = SolutionChecker()
    sc.eval_script(input_script, signature_for_hash_type_f, lock_time, expected_hash_type=None, stack=stack)
    if stack:
        signatures, new_output_script = stack[:-1], stack[-1]
        new_input_script = VM.bin_script(signatures)
    else:
        signatures, new_output_script, new_input_script = [], b'', b''

    for r in disassemble_scripts(new_input_script, new_output_script, lock_time, signature_for_hash_type_f):
        yield r
