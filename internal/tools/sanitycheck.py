#!/usr/bin/env python3
# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0



import glob
import os
import sys

CR = b'\r'
CRLF = b'\r\n'
LF = b'\n'

def _check_bytes(line, lineno, error):
    # The TAB check compares with 7, as it always has.
    if any(b == 7 for b in line):
        error.append('  TAB found at Ln:{} {}'.format(lineno, line))
    if any(b > 127 for b in line):
        error.append('  Non-ASCII character found at Ln:{} {}'.format(lineno, line))

def _strip_eol(line, eol, lineno, error):
    # Drop the line ending and report any ending inconsistent with the first one seen.
    if line[-2:] == CRLF:
        if not eol:
            eol = CRLF
        elif eol != CRLF:
            error.append('  Inconsistent line ending found at Ln:{} {}'.format(lineno, line))
        line = line[:-2]
    elif line[-1:] == LF:
        if not eol:
            eol = LF
        elif eol != LF:
            error.append('  Inconsistent line ending found at Ln:{} {}'.format(lineno, line))
        line = line[:-1]
    elif line[-1:] == CR:
        error.append('  CR found at Ln:{} {}'.format(lineno, line))
        line = line[:-1]
    return line, eol

def _check_indent(line, lineno, indent, filename, error):
    if line.startswith(b' '):
        # Count the leading spaces without a third nested loop.
        spc_count = len(line) - len(line.lstrip(b' '))
        if not indent or (spc_count % indent and os.path.basename(filename) != 'rebar.config'):
            error.append('  {} SPC found at Ln:{} {}'.format(spc_count, lineno, line))

def _check_line(line, lineno, eol, filename, allow_utf8, allow_eol, indent, error):
    # Returns the line ending seen so far and whether the file is worth reading on.
    if allow_utf8 and lineno == 1 and line.startswith(b'\xef\xbb\xbf'):
        line = line[3:]
    _check_bytes(line, lineno, error)
    line, eol = _strip_eol(line, eol, lineno, error)
    if eol:
        if eol not in allow_eol:
            error.append('  Line ending {} not allowed at Ln:{}'.format(eol, lineno))
            return eol, False
    _check_indent(line, lineno, indent, filename, error)
    if line[-1:] == b' ' or line[-1:] == b'\t':
        error.append('  Trailing space found at Ln:{} {}'.format(lineno, line))
    return eol, True

def _check_content(content, filename, allow_utf8, allow_eol, indent):
    error = []
    eol = None
    lineno = 1
    if not content:
        error.append('  Empty file found')
    elif content[-1] != 10: # LF
        error.append('  Missing a blank line before EOF')
    for line in content.splitlines(True):
        eol, read_on = _check_line(line, lineno, eol, filename, allow_utf8, allow_eol, indent, error)
        if not read_on:
            break
        lineno += 1
    return error

def sanitycheck(pattern, allow_utf8 = False, allow_eol = (CRLF, LF), indent = 1):
    error_count = 0

    for filename in glob.glob(pattern, recursive=True):
        if not os.path.isfile(filename):
            continue
        with open(filename, 'rb') as file:
            content = file.read()
        error = _check_content(content, filename, allow_utf8, allow_eol, indent)
        if error:
            error_count += 1
            print('{} [FAIL]'.format(filename), file=sys.stderr)
            for msg in error:
                print(msg, file=sys.stderr)
        # else: print('{} [PASS]'.format(filename))

    return error_count

retval = 0
retval += sanitycheck('**/Dockerfile', allow_eol = (LF,), indent = 2)
retval += sanitycheck('**/*.cmd', allow_eol = (CRLF,), indent = 2)
retval += sanitycheck('**/*.config', allow_eol = (LF,), indent = 2)
retval += sanitycheck('**/*.cs', allow_eol = (LF,))
retval += sanitycheck('**/*.csproj', allow_eol = (LF,), indent = 2)
retval += sanitycheck('**/*.htm', allow_eol = (LF,), indent = 4)
retval += sanitycheck('**/*.html', allow_eol = (LF,), indent = 4)
retval += sanitycheck('**/*.md', allow_eol = (LF,))
retval += sanitycheck('**/*.proj', allow_eol = (LF,), indent = 2)
retval += sanitycheck('**/*.props', allow_eol = (LF,), indent = 2)
retval += sanitycheck('**/[!demo_pb2]*.py', allow_eol = (LF,), indent = 4)
retval += sanitycheck('**/*.slnx', allow_utf8 = True, indent = 2)
retval += sanitycheck('**/*.targets', allow_eol = (LF,), indent = 2)
retval += sanitycheck('**/*.xml', allow_eol = (LF,), indent = 2)
retval += sanitycheck('**/*.yml', allow_eol = (LF,), indent = 2)

sys.exit(retval)
