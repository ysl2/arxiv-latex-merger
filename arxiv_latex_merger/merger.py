import json
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
    source_root_dir = Path(directory)

    readme_main_tex_path = _main_tex_file_from_arxiv_readme(source_root_dir)
    if readme_main_tex_path:
        return str(readme_main_tex_path)

    tex_file_paths = []

    for root, _, files in os.walk(directory):
        for file in files:
            file_path = os.path.join(root, file)
            if file.endswith('.tex'):
                tex_file_paths.append(file_path)
                tex_file, _ = read_tex_file(file_path)
                for line in tex_file:
                    if documentclass_pattern.search(line):
                        return file_path

    # special case for some old submissions, they are already merged
    if len(tex_file_paths) == 1:
        print(f"Detected single TeX file for {directory}, please verify that this is correct...")
        return tex_file_paths[0]

    raise FileNotFoundError(f"No main .tex file found in the specified directory {directory}")


def _main_tex_file_from_arxiv_readme(source_root_dir):
    readme = _read_arxiv_readme(source_root_dir)
    if not readme:
        return None

    for source in readme.get('sources', []):
        if source.get('usage') != 'toplevel':
            continue

        filename = source.get('filename')
        if not filename:
            continue

        source_path = source_root_dir / filename
        if source_path.is_file() and source_path.suffix == '.tex':
            return source_path

    return None


def _read_arxiv_readme(source_root_dir):
    readme_path = Path(source_root_dir) / '00README.json'
    if not readme_path.is_file():
        return None

    try:
        return json.loads(readme_path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return None


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

    if not file_path.endswith('.tex'):
        yield f"{file_path}.tex"


_PRESERVED_SYSTEM_INPUT_NAMES = {
    'glyphtounicode',
}


def _should_preserve_missing_input(input_relative_path):
    normalized_path = os.path.normpath(input_relative_path)

    if os.path.isabs(normalized_path):
        return False

    if normalized_path.startswith('..') or os.sep in normalized_path:
        return False

    input_name = normalized_path
    if input_name.endswith('.tex'):
        input_name = input_name[:-len('.tex')]

    return input_name in _PRESERVED_SYSTEM_INPUT_NAMES


def _find_source_root_dir(start_dir):
    current_dir = os.path.abspath(start_dir or '.')

    while True:
        if os.path.isfile(os.path.join(current_dir, '00README.json')):
            return current_dir

        parent_dir = os.path.dirname(current_dir)
        if parent_dir == current_dir:
            return os.path.abspath(start_dir or '.')

        current_dir = parent_dir


def _deduplicate_paths(paths):
    seen_paths = set()

    for path in paths:
        normalized_path = os.path.abspath(path)
        if normalized_path in seen_paths:
            continue

        seen_paths.add(normalized_path)
        yield path


def _input_base_path_candidates(input_relative_path, root_dir, source_root_dir):
    if os.path.isabs(input_relative_path):
        return [os.path.normpath(input_relative_path)]

    return list(_deduplicate_paths([
        os.path.normpath(os.path.join(root_dir, input_relative_path)),
        os.path.normpath(os.path.join(source_root_dir, input_relative_path)),
    ]))


def _resolve_input_file_path(input_relative_path, file_dir, root_dir, source_root_dir):
    candidate_paths = []
    for base_path in _input_base_path_candidates(input_relative_path, root_dir, source_root_dir):
        candidate_paths.extend(_input_path_candidates(base_path))

    candidate_paths = list(_deduplicate_paths(candidate_paths))
    for candidate_path in candidate_paths:
        if os.path.isfile(candidate_path):
            return candidate_path

    if _should_preserve_missing_input(input_relative_path):
        return None

    expected_paths = ', '.join(candidate_paths)
    root_display = root_dir or '.'
    raise FileNotFoundError(
        f"Could not resolve \\input{{{input_relative_path}}} from {file_dir}. "
        f"Expected {expected_paths} relative to {root_display}."
    )


def _process_input_commands_in_line(line, input_pattern, file_dir, root_dir, source_root_dir):
    input_matches = list(_active_pattern_matches(input_pattern, line))

    if not input_matches:
        return line

    line_parts = []
    previous_end = 0

    for match in input_matches:
        line_parts.append(line[previous_end:match.start()])
        input_relative_path = match.group(1).strip().replace('\\', '/')
        input_file_path = _resolve_input_file_path(input_relative_path, file_dir, root_dir, source_root_dir)
        if input_file_path is None:
            line_parts.append(match.group(0))
            previous_end = match.end()
            continue

        input_file_dir = os.path.dirname(input_file_path)
        input_file_lines, _ = read_tex_file(input_file_path)

        input_file_content = process_input_commands(input_file_lines, input_file_dir, root_dir, source_root_dir)
        line_parts.append(''.join(input_file_content))
        previous_end = match.end()

    line_parts.append(line[previous_end:])
    return ''.join(line_parts)


def process_input_commands(file_lines, file_dir, root_dir=None, source_root_dir=None):
    if root_dir is None:
        root_dir = file_dir
    if source_root_dir is None:
        source_root_dir = _find_source_root_dir(root_dir)

    input_pattern = re.compile(r'\\input\{(.+?)\}')
    output_lines = []
    in_comment_environment = False
    literal_environment = None

    for line in file_lines:
        if in_comment_environment:
            output_lines.append(line)
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
            prefix = line[:comment_begin.start()]
            suffix = line[comment_begin.start():]
            output_lines.append(
                _process_input_commands_in_line(prefix, input_pattern, file_dir, root_dir, source_root_dir) + suffix
            )
            if not _first_active_environment_match(line, 'end', {'comment'}, start_after=comment_begin.start()):
                in_comment_environment = True
            continue

        if literal_begin:
            prefix = line[:literal_begin.start()]
            suffix = line[literal_begin.start():]
            output_lines.append(
                _process_input_commands_in_line(prefix, input_pattern, file_dir, root_dir, source_root_dir) + suffix
            )
            literal_environment = literal_begin.group(1)
            if _first_active_environment_match(line, 'end', {literal_environment}, start_after=literal_begin.start()):
                literal_environment = None
            continue

        output_lines.append(
            _process_input_commands_in_line(line, input_pattern, file_dir, root_dir, source_root_dir)
        )

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


def _bibliography_path_candidates_with_work_dirs(bib_name, root_dir, source_root_dir):
    bib_name = bib_name.strip().replace('\\', '/')
    if not bib_name:
        return []

    if os.path.isabs(bib_name):
        candidate_paths = [(os.path.normpath(bib_name), root_dir)]
    else:
        candidate_paths = [
            (os.path.normpath(os.path.join(root_dir, bib_name)), root_dir),
            (os.path.normpath(os.path.join(source_root_dir, bib_name)), source_root_dir),
        ]

    seen_paths = set()
    output_paths = []
    for path, work_dir in candidate_paths:
        if not path.endswith('.bib'):
            path += '.bib'

        path_abs = os.path.abspath(path)
        if path_abs in seen_paths:
            continue

        seen_paths.add(path_abs)
        output_paths.append((path, work_dir))

    return output_paths


def _bibliography_source_work_dirs(bibliography_commands, root_dir, source_root_dir):
    source_work_dirs = []

    for command in bibliography_commands:
        for bib_name in command.split(','):
            for bib_path, work_dir in _bibliography_path_candidates_with_work_dirs(
                bib_name,
                root_dir,
                source_root_dir,
            ):
                if os.path.isfile(bib_path):
                    source_work_dirs.append(work_dir)
                    break

    return list(_deduplicate_paths(source_work_dirs))


def _bbl_file_path_candidates(main_tex_path, source_root_dir):
    main_tex_bbl_path = os.path.splitext(main_tex_path)[0] + '.bbl'
    bbl_filename = os.path.splitext(os.path.basename(main_tex_path))[0] + '.bbl'
    source_root_bbl_path = os.path.join(source_root_dir, bbl_filename)

    return list(_deduplicate_paths([main_tex_bbl_path, source_root_bbl_path]))


def _first_existing_path(paths):
    for path in paths:
        if os.path.isfile(path):
            return path

    return False


def _latexmk_run_candidates(main_tex_path, source_root_dir, preferred_work_dirs=None):
    main_tex_abs_path = os.path.abspath(main_tex_path)
    main_tex_dir = os.path.dirname(main_tex_abs_path) or os.path.abspath('.')
    work_dirs = list(preferred_work_dirs or [])
    work_dirs.extend([main_tex_dir, source_root_dir])

    seen = set()
    for work_dir in work_dirs:
        work_dir_abs_path = os.path.abspath(work_dir or '.')
        if work_dir_abs_path in seen:
            continue

        seen.add(work_dir_abs_path)
        try:
            common_path = os.path.commonpath([main_tex_abs_path, work_dir_abs_path])
        except ValueError:
            common_path = None

        if common_path == work_dir_abs_path:
            main_tex_argument = os.path.relpath(main_tex_abs_path, work_dir_abs_path)
        else:
            main_tex_argument = main_tex_abs_path

        yield work_dir_abs_path, main_tex_argument


def _latexmk_pdf_mode_arg(source_root_dir):
    readme = _read_arxiv_readme(source_root_dir)
    compiler = (readme or {}).get('process', {}).get('compiler', '').lower()

    if compiler == 'xelatex':
        return '-pdfxe'
    if compiler == 'lualatex':
        return '-pdflua'

    return '-pdf'


def _generate_bbl_file(main_tex_path, source_root_dir, preferred_work_dirs=None):
    any_success = False
    latexmk_pdf_mode_arg = _latexmk_pdf_mode_arg(source_root_dir)

    for work_dir, main_tex_argument in _latexmk_run_candidates(main_tex_path, source_root_dir, preferred_work_dirs):
        try:
            result = subprocess.run(
                [
                    'latexmk',
                    latexmk_pdf_mode_arg,
                    '-interaction=nonstopmode',
                    '-halt-on-error',
                    main_tex_argument,
                ],
                cwd=work_dir,
                check=False,
                capture_output=True,
            )
        except FileNotFoundError:
            print("Warning: latexmk was not found; bibliography could not be inlined.")
            return False

        if result.returncode == 0:
            any_success = True
            break

    if not any_success:
        print(f"Warning: latexmk failed for {main_tex_path}; bibliography could not be inlined.")

    return any_success


def process_bibliography_commands(file_lines, main_tex_path):
    bibliography_commands = _active_bibliography_commands(file_lines)

    if not bibliography_commands:
        return file_lines

    main_tex_dir = os.path.dirname(main_tex_path)
    source_root_dir = _find_source_root_dir(main_tex_dir)
    bbl_file_paths = _bbl_file_path_candidates(main_tex_path, source_root_dir)
    bbl_file_path = _first_existing_path(bbl_file_paths)

    if not bbl_file_path:
        bibliography_work_dirs = _bibliography_source_work_dirs(
            bibliography_commands,
            main_tex_dir,
            source_root_dir,
        )
        if bibliography_work_dirs:
            _generate_bbl_file(main_tex_path, source_root_dir, preferred_work_dirs=bibliography_work_dirs)
            bbl_file_path = _first_existing_path(bbl_file_paths)
        else:
            print(f"Warning: bibliography command found in {main_tex_path}, but no .bib file was found.")

    if not bbl_file_path:
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
