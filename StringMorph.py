#!/usr/bin/python3
# https://github.com/jph4cks

import sys
import argparse
import csv
import random
import os
import hashlib

def get_sha256_hash(file_path):
    sha256_hash = hashlib.sha256()
    try:
        with open(file_path, "rb") as file:
            for byte_block in iter(lambda: file.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()
    except FileNotFoundError:
        return "File not found"
    except Exception as e:
        return str(e)

def print_hash(file_path):
    file_name = os.path.basename(file_path)
    hash_value = get_sha256_hash(file_path)
    print(f'\tSHA256: {hash_value}\t{file_name}')

def generate_random_string(original_string, single_char=''):
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

def modify_binary_file(original_file, csv_file, is_test=False, single_char=''):
    print(f"\n[+] Binary Modification Task:")
    
    with open(csv_file, 'r') as csvfile:
        reader = csv.reader(csvfile)
        next(reader)  # Skip header row
        modifications = {int(row[0], 16): (row[1], generate_random_string(row[1], single_char)) for row in reader}

    output_file = './.tmpfile' if is_test else os.path.splitext(original_file)[0] + "_modified" + os.path.splitext(original_file)[1]
    
    with open(original_file, 'rb') as infile, open(output_file, 'wb') as outfile:
        pos = 0
        while True:
            byte = infile.read(1)
            if not byte:
                break
            if pos in modifications:
                original, modified = modifications[pos]
                print(f"\t{pos}: {original} -> {modified}")
                string_to_write = modified.encode()
                outfile.write(string_to_write)
                infile.seek(pos + len(string_to_write))
                pos += len(string_to_write)
            else:
                outfile.write(byte)
                pos += 1

    if not is_test:
        print(f"\t  Binary file {original_file} modified and saved as {output_file}\n")
        print_hash(original_file)
        print_hash(output_file)
    else:
        print(f"\t  Modifications displayed in test mode, no file was saved.")
        os.remove(output_file)

def find_ascii_strings(filename, min_length, keywords=None, no_space=False):
    print(f"\n[+] Interesting Strings:")
    strings = []

    with open(filename, 'rb') as file:
        binary_data = file.read()
        current_string = ''
        current_position = None

        for i, byte in enumerate(binary_data):
            if 32 <= byte <= 126 and (byte != 32 or not no_space):  # ASCII printable characters, space is considered if no_space is False
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

def main():
    parser = argparse.ArgumentParser(description='Find ASCII strings in a binary file and optionally modify the file.')
    parser.add_argument('filename', type=str, help='The binary file to inspect')
    parser.add_argument('-l', '--length', type=int, default=7, help='Minimum length of ASCII strings to consider')
    parser.add_argument('-k', '--keywords', type=str, help='Comma-separated list of keywords to filter strings')
    parser.add_argument('-v', '--verbose', action='store_true', help='Print output to terminal')
    parser.add_argument('-o', '--output', type=str, default='output.csv', help='Specify the output file name')
    parser.add_argument('--no-space', action='store_true', help='Consider strings separated by spaces as individual strings')
    parser.add_argument('-e', '--execute', action='store_true', help='Modify the binary file')
    parser.add_argument('-t', '--test', action='store_true', help='Modify the binary file in test mode')
    parser.add_argument('--single-char', type=str, default='', help='Substitute only by that character, numbers will always be 4')
    parser.add_argument('-s', '--sourcefile', type=str, help='Specify a CSV file to use for binary modifications instead of extracting strings')

    args = parser.parse_args()

    if args.sourcefile:
        # If sourcefile.csv is provided, use it for binary modifications
        print(f"\n[+] Using source file: {args.sourcefile}")
        if args.execute or args.test:
            modify_binary_file(args.filename, args.sourcefile, args.test, args.single_char)
    else:
        # Otherwise, proceed with the usual ASCII string extraction and optional binary modification
        keywords = args.keywords.split(',') if args.keywords else None
        min_length = args.length if not keywords else min(len(k) for k in keywords)

        strings = find_ascii_strings(args.filename, min_length, keywords, args.no_space)

        with open(args.output, 'w', newline='', encoding='utf-8') as csvfile:
            writer = csv.writer(csvfile, delimiter=',', quotechar='"', quoting=csv.QUOTE_MINIMAL)
            writer.writerow(['Location', 'String'])

            for location, string in strings:
                writer.writerow([location, string])
                if args.verbose:
                    print(f'\t{location}: {string}')

        print(f"\t  Output written to {args.output}")
        
        if args.execute or args.test:
            modify_binary_file(args.filename, args.output, args.test, args.single_char)

if __name__ == "__main__":
    main()
