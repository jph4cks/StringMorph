#!/usr/bin/python3
# https://github.com/jph4cks

import sys
import argparse
import csv
import random
import os
import hashlib
import io
import re
import stat
import tempfile

def get_sha256_hash(file_path):
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as file:
        for byte_block in iter(lambda: file.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()

def print_hash(file_path):
    file_name = os.path.basename(file_path)
    hash_value = get_sha256_hash(file_path)
    print(f'\tSHA256: {hash_value}\t{file_name}')

def validate_single_char(single_char):
    if single_char and (len(single_char) != 1 or not 32 <= ord(single_char) <= 126):
        raise ValueError('--single-char must be one printable ASCII character')


def generate_random_string(original_string, single_char=''):
    validate_single_char(single_char)
    if any(not 32 <= ord(char) <= 126 for char in original_string):
        raise ValueError('Only printable ASCII strings are supported')
    new_string = ''
    for char in original_string:
        if char.isalpha():  # Replace letters
            if single_char:
                new_string += single_char  # Replace with 'A' if single_char is True
            else:
                new_string += random.choice('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ')
        elif char.isdigit():  # Replace digits with random digits
            if single_char:
                new_string += '4'
            else:
                new_string += random.choice('0123456789')
        else:
            new_string += char  # Keep non-alphanumeric characters unchanged
    return new_string

def read_binary_snapshot(filename):
    with open(filename, 'rb') as file:
        mode = stat.S_IMODE(os.fstat(file.fileno()).st_mode)
        return file.read(), mode


def read_csv_rows(csv_file):
    with open(csv_file, 'r', newline='', encoding='utf-8-sig') as csvfile:
        reader = csv.reader(csvfile, strict=True)
        if next(reader, None) != ['Location', 'String']:
            raise ValueError('CSV header must be exactly Location,String')
        return list(reader)


def build_modification_plan(binary_data, rows, single_char=''):
    """Validate the entire plan against the same bytes that will be copied."""
    validate_single_char(single_char)
    ranges = []
    for row_number, row in enumerate(rows, start=2):
        if len(row) != 2:
            raise ValueError(f'CSV row {row_number}: expected exactly two columns')
        location, original = row
        if not re.fullmatch(r'(?:0[xX])?[0-9a-fA-F]+', location.strip()):
            raise ValueError(f'CSV row {row_number}: expected a nonnegative hexadecimal offset')
        offset = int(location, 16)
        if not original or any(not 32 <= ord(char) <= 126 for char in original):
            raise ValueError(f'CSV row {row_number}: string must be nonempty printable ASCII')
        original_bytes = original.encode('ascii')
        end = offset + len(original_bytes)
        if end > len(binary_data):
            raise ValueError(f'CSV row {row_number}: range at {hex(offset)} exceeds the input size')
        if binary_data[offset:end] != original_bytes:
            raise ValueError(f'CSV row {row_number}: original bytes do not match at {hex(offset)}')
        ranges.append((offset, end, original, row_number))

    ranges.sort()
    previous_end = 0
    plan = []
    for offset, end, original, row_number in ranges:
        if offset < previous_end:
            raise ValueError(f'CSV row {row_number}: duplicate or overlapping range at {hex(offset)}')
        modified = generate_random_string(original, single_char)
        if len(modified.encode('ascii')) != end - offset:
            raise ValueError(f'CSV row {row_number}: replacement changes the byte length')
        plan.append((offset, original, modified))
        previous_end = end
    return plan


def cleanup_publication_files(staged, published=()):
    """Attempt every owned-file cleanup and return paths that could not be removed."""
    failures = []
    for temporary, path in reversed(published):
        try:
            if os.path.exists(path) and os.path.samefile(temporary, path):
                os.unlink(path)
        except OSError as error:
            failures.append(f'{path}: {error}')
    for temporary, path in staged:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        except OSError as error:
            failures.append(f'{temporary}: {error}')
    return failures


def publish_new_files(outputs):
    """Stage outputs completely, then publish with exclusive hard links.

    Each output is (path, bytes, optional POSIX mode). Existing paths are never
    replaced. On failure, attempt to roll back this call's published files and
    report any cleanup failures. This is not a crash-atomic transaction.
    """
    destinations = set()
    for path, contents, mode in outputs:
        resolved = os.path.normcase(os.path.realpath(path))
        if resolved in destinations:
            raise ValueError('CSV and binary outputs must use different paths')
        destinations.add(resolved)
        if os.path.lexists(path):
            raise FileExistsError(f'Output already exists: {path}; choose a new path')
        if not os.path.isdir(os.path.dirname(os.path.abspath(path))):
            raise FileNotFoundError(f'Output directory does not exist: {path}')

    staged = []
    published = []
    try:
        for path, contents, mode in outputs:
            temporary = tempfile.NamedTemporaryFile(
                prefix='.stringmorph-', suffix='.tmp',
                dir=os.path.dirname(os.path.abspath(path)), delete=False)
            staged.append((temporary.name, path))
            with temporary as file:
                file.write(contents)
                file.flush()
                os.fsync(file.fileno())
            if mode is not None and os.name == 'posix':
                os.chmod(temporary.name, mode)
        for temporary, path in staged:
            # Unlike replace/rename, link never overwrites an existing path.
            os.link(temporary, path)
            published.append((temporary, path))
    except BaseException:
        failures = cleanup_publication_files(staged, published)
        if failures:
            print('Cleanup incomplete; files may remain:\n' + '\n'.join(failures), file=sys.stderr)
        raise
    else:
        failures = cleanup_publication_files(staged)
        if failures:
            print('Outputs saved; could not remove temporary files:\n' + '\n'.join(failures), file=sys.stderr)


def modify_binary_snapshot(original_file, binary_data, mode, rows, is_test=False,
                           single_char='', csv_output=None):
    plan = build_modification_plan(binary_data, rows, single_char)
    print('\n[+] Binary Modification Task:')
    print('\tByte checks preserve layout, not application functionality.')
    for offset, original, modified in plan:
        print(f'\t{hex(offset)}: {original} -> {modified}')
    if is_test:
        print('\tPreview complete; no files were created or changed.')
        return

    modified_data = bytearray(binary_data)
    for offset, original, modified in plan:
        modified_data[offset:offset + len(original)] = modified.encode('ascii')
    stem, extension = os.path.splitext(original_file)
    output_file = stem + '_modified' + extension
    outputs = [(output_file, modified_data, mode)]
    if csv_output is not None:
        outputs.append((csv_output[0], csv_output[1], None))
    publish_new_files(outputs)
    print(f'\tBinary copy saved as {output_file}')
    print(f'\tSHA256: {hashlib.sha256(binary_data).hexdigest()}\t{os.path.basename(original_file)}')
    print(f'\tSHA256: {hashlib.sha256(modified_data).hexdigest()}\t{os.path.basename(output_file)}')


def modify_binary_file(original_file, csv_file, is_test=False, single_char=''):
    binary_data, mode = read_binary_snapshot(original_file)
    modify_binary_snapshot(original_file, binary_data, mode, read_csv_rows(csv_file),
                           is_test, single_char)

def find_ascii_strings(filename, min_length, keywords=None, no_space=False):
    binary_data, _ = read_binary_snapshot(filename)
    return extract_ascii_strings(binary_data, min_length, keywords, no_space)


def extract_ascii_strings(binary_data, min_length, keywords=None, no_space=False):
    if min_length < 1:
        raise ValueError('Minimum string length must be positive')
    if keywords is not None and any(not keyword.strip() for keyword in keywords):
        raise ValueError('Keyword elements must not be empty')
    print(f"\n[+] Interesting Strings:")
    strings = []
    current_string = ''
    current_position = None
    for i, byte in enumerate(binary_data):
        if 32 <= byte <= 126 and (byte != 32 or not no_space):
            if current_string == '':
                current_position = hex(i)
            current_string += chr(byte)
        else:
            if len(current_string) >= min_length:
                if not keywords or any(keyword.lower() in current_string.lower() for keyword in keywords):
                    strings.append((current_position, current_string))
            current_string = ''
    if len(current_string) >= min_length:
        if not keywords or any(keyword.lower() in current_string.lower() for keyword in keywords):
            strings.append((current_position, current_string))
    return strings


def run(args):
    validate_single_char(args.single_char)
    if args.length < 1:
        raise ValueError('--length must be positive')
    keywords = [keyword.strip() for keyword in args.keywords.split(',')] if args.keywords is not None else None
    if keywords is not None and any(not keyword for keyword in keywords):
        raise ValueError('--keywords must not contain empty elements')

    if args.sourcefile:
        if not (args.execute or args.test):
            raise ValueError('--sourcefile requires --execute or --test')
        modify_binary_file(args.filename, args.sourcefile, args.test, args.single_char)
        return

    binary_data, mode = read_binary_snapshot(args.filename)
    min_length = args.length if not keywords else min(len(keyword) for keyword in keywords)
    strings = extract_ascii_strings(binary_data, min_length, keywords, args.no_space)
    if args.verbose:
        for location, string in strings:
            print(f'\t{location}: {string}')

    if args.test:
        modify_binary_snapshot(args.filename, binary_data, mode, strings, True, args.single_char)
        return

    csv_buffer = io.StringIO(newline='')
    writer = csv.writer(csv_buffer)
    writer.writerow(['Location', 'String'])
    writer.writerows(strings)
    csv_data = csv_buffer.getvalue().encode('utf-8')
    if args.execute:
        modify_binary_snapshot(args.filename, binary_data, mode, strings, False,
                               args.single_char, (args.output, csv_data))
    else:
        publish_new_files([(args.output, csv_data, None)])
    print(f'\tOutput written to {args.output}')

def main():
    parser = argparse.ArgumentParser(
        description='Extract ASCII strings or replace selected bytes in a new binary copy. '
                    'Byte-length preservation does not guarantee application functionality.')
    parser.add_argument('filename', type=str, help='The binary file to inspect')
    parser.add_argument('-l', '--length', type=int, default=7, help='Minimum length of ASCII strings to consider')
    parser.add_argument('-k', '--keywords', type=str, help='Comma-separated list of keywords to filter strings')
    parser.add_argument('-v', '--verbose', action='store_true', help='Print output to terminal')
    parser.add_argument('-o', '--output', type=str, default='output.csv', help='New output CSV path; never overwrite existing files')
    parser.add_argument('--no-space', action='store_true', help='Consider strings separated by spaces as individual strings')
    action = parser.add_mutually_exclusive_group()
    action.add_argument('-e', '--execute', action='store_true', help='Write a new modified binary copy')
    action.add_argument('-t', '--test', action='store_true', help='Validate and preview replacements without any file writes')
    parser.add_argument('--single-char', type=str, default='', help='One printable ASCII replacement for letters; digits become 4')
    parser.add_argument('-s', '--sourcefile', type=str, help='Specify a CSV file to use for binary modifications instead of extracting strings')

    args = parser.parse_args()

    try:
        run(args)
    except (OSError, ValueError, csv.Error) as error:
        parser.exit(1, f'error: {error}\n')

if __name__ == "__main__":
    main()
