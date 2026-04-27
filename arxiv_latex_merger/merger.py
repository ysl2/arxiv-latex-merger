import os
import re
import shutil
import subprocess
from pathlib import Path

def read_tex_file(file_path):
    try:
        with open(file_path, 'r', encoding='utf-8') as file:
            return file.readlines(), 'utf-8'
    except Exception as e:
        # assuming that non-utf-8 files that can be processed here are latin-1
        # no other cases found yet
        with open(file_path, 'r', encoding='latin-1') as file:
            return file.readlines(), 'latin-1'

def find_main_tex_file(directory):
    documentclass_pattern = re.compile(r'\\documentclass')

    for root, _, files in os.walk(directory):
        # special case for some old submissions, they are already merged
        if len(files)==1:
            print(f"Detected single file for {directory}, please verify that this is correct...")
            file_path = os.path.join(root, files[0])
            return file_path
        
        for file in files:
            file_path = os.path.join(root, file)
            if file.endswith('.tex'):
                tex_file, _ = read_tex_file(file_path)
                for line in tex_file:
                    if documentclass_pattern.search(line):
                        return file_path

    raise FileNotFoundError(f"No main .tex file found in the specified directory {directory}")

def process_input_commands(file_lines, file_dir):
    input_pattern = re.compile(r'\\input\{(.+?)\}')
    output_lines = []

    for line in file_lines:
        if not line.strip().startswith('%'):
            while match := input_pattern.search(line):
                input_relative_path = match.group(1).replace('\\', '/')
                input_file_path = os.path.normpath(os.path.join(file_dir, input_relative_path))

                if not input_file_path.endswith('.tex'):
                    input_file_path += '.tex'

                if not os.path.isfile(input_file_path):
                    input_file_path = os.path.normpath(os.path.join(file_dir, '..', input_relative_path))
                    if not input_file_path.endswith('.tex'):
                        input_file_path += '.tex'

                input_file_dir = os.path.dirname(input_file_path)
                input_file_lines, _ = read_tex_file(input_file_path)

                input_file_content = process_input_commands(input_file_lines, input_file_dir)

                line = line[:match.start()] + ''.join(input_file_content) + line[match.end():]

        output_lines.append(line)

    return output_lines

def _is_commented_line(line):
    return line.strip().startswith('%')


def _active_bibliography_commands(file_lines):
    bibliography_pattern = re.compile(r'\\bibliography\{(.+?)\}')
    commands = []

    for line in file_lines:
        if _is_commented_line(line):
            continue
        commands.extend(bibliography_pattern.findall(line))

    return commands


def _bibliography_sources_exist(bibliography_commands, file_dir):
    for command in bibliography_commands:
        for bib_name in command.split(','):
            bib_name = bib_name.strip().replace('\\', '/')
            if not bib_name:
                continue

            bib_path = os.path.normpath(os.path.join(file_dir, bib_name))
            if not bib_path.endswith('.bib'):
                bib_path += '.bib'

            if os.path.isfile(bib_path):
                return True

    return False


def _generate_bbl_file(main_tex_path):
    main_tex_dir = os.path.dirname(main_tex_path) or '.'
    main_tex_filename = os.path.basename(main_tex_path)

    try:
        result = subprocess.run(
            [
                'latexmk',
                '-pdf',
                '-interaction=nonstopmode',
                '-halt-on-error',
                main_tex_filename,
            ],
            cwd=main_tex_dir,
            check=False,
            capture_output=True,
        )
    except FileNotFoundError:
        print("Warning: latexmk was not found; bibliography could not be inlined.")
        return False

    if result.returncode != 0:
        print(f"Warning: latexmk failed for {main_tex_path}; bibliography could not be inlined.")
        return False

    return True


def process_bibliography_commands(file_lines, main_tex_path):
    bibliography_commands = _active_bibliography_commands(file_lines)

    if not bibliography_commands:
        return file_lines

    main_tex_dir = os.path.dirname(main_tex_path)
    bbl_file_path = os.path.splitext(main_tex_path)[0] + '.bbl'

    if not os.path.isfile(bbl_file_path):
        if _bibliography_sources_exist(bibliography_commands, main_tex_dir):
            _generate_bbl_file(main_tex_path)
        else:
            print(f"Warning: bibliography command found in {main_tex_path}, but no .bib file was found.")

    if not os.path.isfile(bbl_file_path):
        print(f"Warning: bibliography command found in {main_tex_path}, but no .bbl file was available to inline.")
        return file_lines

    bbl_file_lines, _ = read_tex_file(bbl_file_path)
    bibliography_pattern = re.compile(r'\\bibliography\{.+?\}')
    bibliography_style_pattern = re.compile(r'\\bibliographystyle\{.+?\}')
    output_lines = []

    for line in file_lines:
        if _is_commented_line(line):
            output_lines.append(line)
            continue

        if bibliography_style_pattern.search(line):
            line = bibliography_style_pattern.sub('', line)
            if line.strip():
                output_lines.append(line)
            continue

        if bibliography_pattern.search(line):
            output_lines.extend(bbl_file_lines)
            continue

        output_lines.append(line)

    return output_lines


def merge_tex_files(main_tex_path, remove_src=False, merge_bib=True):
    
    main_tex_dir = os.path.dirname(main_tex_path)
    main_tex_lines, encoding = read_tex_file(main_tex_path)
    merged_tex_lines = process_input_commands(main_tex_lines, main_tex_dir)

    if merge_bib:
        merged_tex_lines = process_bibliography_commands(merged_tex_lines, main_tex_path)
        
    if remove_src:
        shutil.rmtree(Path(f"./{main_tex_dir}"))

    return ''.join(merged_tex_lines), encoding
