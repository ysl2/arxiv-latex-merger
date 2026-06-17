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


def _is_escaped(line, index):
    backslash_count = 0
    current_index = index - 1

    while current_index >= 0 and line[current_index] == '\\':
        backslash_count += 1
        current_index -= 1

    return backslash_count % 2 == 1


def _is_in_latex_comment(line, index):
    for match in re.finditer('%', line):
        if not _is_escaped(line, match.start()):
            return match.start() <= index

    return False


def _first_unescaped_percent(line):
    for match in re.finditer('%', line):
        if not _is_escaped(line, match.start()):
            return match.start()

    return None


def _line_ending(line):
    for ending in ('\r\n', '\n', '\r'):
        if line.endswith(ending):
            return ending

    return ''


def _active_pattern_matches(pattern, line):
    for match in pattern.finditer(line):
        if not _is_in_latex_comment(line, match.start()):
            yield match


def _replace_active_matches(line, matches, replacement):
    output_parts = []
    previous_end = 0

    for match in matches:
        output_parts.append(line[previous_end:match.start()])
        output_parts.append(replacement)
        previous_end = match.end()

    output_parts.append(line[previous_end:])
    return ''.join(output_parts)


def _input_path_candidates(file_path):
    yield file_path

    if not os.path.splitext(file_path)[1]:
        yield f"{file_path}.tex"


def _resolve_input_file_path(input_relative_path, file_dir, root_dir):
    if os.path.isabs(input_relative_path):
        input_file_path = os.path.normpath(input_relative_path)
    else:
        input_file_path = os.path.normpath(os.path.join(root_dir, input_relative_path))

    candidate_paths = list(_input_path_candidates(input_file_path))
    for candidate_path in candidate_paths:
        if os.path.isfile(candidate_path):
            return candidate_path

    expected_paths = ', '.join(candidate_paths)
    root_display = root_dir or '.'
    raise FileNotFoundError(
        f"Could not resolve \\input{{{input_relative_path}}} from {file_dir}. "
        f"Expected {expected_paths} relative to {root_display}."
    )


def process_input_commands(file_lines, file_dir, root_dir=None):
    if root_dir is None:
        root_dir = file_dir

    input_pattern = re.compile(r'\\input\{(.+?)\}')
    output_lines = []

    for line in file_lines:
        input_matches = list(_active_pattern_matches(input_pattern, line))

        if not input_matches:
            output_lines.append(line)
            continue

        line_parts = []
        previous_end = 0

        for match in input_matches:
            line_parts.append(line[previous_end:match.start()])
            input_relative_path = match.group(1).replace('\\', '/')
            input_file_path = _resolve_input_file_path(input_relative_path, file_dir, root_dir)
            input_file_dir = os.path.dirname(input_file_path)
            input_file_lines, _ = read_tex_file(input_file_path)

            input_file_content = process_input_commands(input_file_lines, input_file_dir, root_dir)
            line_parts.append(''.join(input_file_content))
            previous_end = match.end()

        line_parts.append(line[previous_end:])
        output_lines.append(''.join(line_parts))

    return output_lines

def _is_commented_line(line):
    first_non_space_index = len(line) - len(line.lstrip())
    return _is_in_latex_comment(line, first_non_space_index)


_LITERAL_ENVIRONMENTS = {
    'verbatim',
    'verbatim*',
    'Verbatim',
    'lstlisting',
    'minted',
    'filecontents',
    'filecontents*',
}


def _first_active_environment_match(line, command, environment_names=None, start_after=None):
    environment_pattern = re.compile(rf'\\{command}\{{([^}}]+)\}}')

    for match in _active_pattern_matches(environment_pattern, line):
        if start_after is not None and match.start() <= start_after:
            continue

        if environment_names is None or match.group(1) in environment_names:
            return match

    return None


def _strip_latex_comment_from_line(line):
    comment_start = _first_unescaped_percent(line)

    if comment_start is None:
        return line

    prefix = line[:comment_start]
    ending = _line_ending(line)
    comment_body = line[comment_start + 1:]
    if ending:
        comment_body = comment_body[:-len(ending)]

    if not prefix.strip():
        return None

    if not comment_body.strip() or not prefix[-1].isspace():
        return f"{prefix}%{ending}"

    return f"{prefix.rstrip()}{ending}"


def remove_latex_comments(file_lines):
    output_lines = []
    in_comment_environment = False
    literal_environment = None

    for line in file_lines:
        if in_comment_environment:
            if _first_active_environment_match(line, 'end', {'comment'}):
                in_comment_environment = False
            continue

        if literal_environment:
            output_lines.append(line)
            if _first_active_environment_match(line, 'end', {literal_environment}):
                literal_environment = None
            continue

        comment_begin = _first_active_environment_match(line, 'begin', {'comment'})
        literal_begin = _first_active_environment_match(line, 'begin', _LITERAL_ENVIRONMENTS)

        if comment_begin and (not literal_begin or comment_begin.start() < literal_begin.start()):
            if not _first_active_environment_match(line, 'end', {'comment'}, start_after=comment_begin.start()):
                in_comment_environment = True
            continue

        if literal_begin:
            output_lines.append(line)
            literal_environment = literal_begin.group(1)
            if _first_active_environment_match(line, 'end', {literal_environment}, start_after=literal_begin.start()):
                literal_environment = None
            continue

        stripped_line = _strip_latex_comment_from_line(line)
        if stripped_line is not None:
            output_lines.append(stripped_line)

    return output_lines


def _active_bibliography_commands(file_lines):
    bibliography_pattern = re.compile(r'\\bibliography\{(.+?)\}')
    commands = []

    for line in file_lines:
        commands.extend(match.group(1) for match in _active_pattern_matches(bibliography_pattern, line))

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
        bibliography_matches = list(_active_pattern_matches(bibliography_pattern, line))
        if bibliography_matches:
            output_lines.extend(bbl_file_lines)
            continue

        bibliography_style_matches = list(_active_pattern_matches(bibliography_style_pattern, line))
        if bibliography_style_matches:
            line = _replace_active_matches(line, bibliography_style_matches, '')
            if line.strip():
                output_lines.append(line)
            continue

        output_lines.append(line)

    return output_lines


def merge_tex_files(main_tex_path, remove_src=False, merge_bib=True, remove_comments=False):
    
    main_tex_dir = os.path.dirname(main_tex_path)
    main_tex_lines, encoding = read_tex_file(main_tex_path)
    merged_tex_lines = process_input_commands(main_tex_lines, main_tex_dir)

    if merge_bib:
        merged_tex_lines = process_bibliography_commands(merged_tex_lines, main_tex_path)

    if remove_comments:
        merged_tex_lines = remove_latex_comments(merged_tex_lines)
        
    if remove_src:
        shutil.rmtree(Path(f"./{main_tex_dir}"))

    return ''.join(merged_tex_lines), encoding
