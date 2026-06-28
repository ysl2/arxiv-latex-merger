import json
import os
import re
import signal
import shutil
import subprocess
import tempfile
from pathlib import Path


LATEXMK_TIMEOUT_SECONDS = 300
LATEXMK_TERMINATION_GRACE_SECONDS = 5

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


_TEX_CONDITIONAL_PATTERN = re.compile(r'\\(if[a-zA-Z@]*|fi)(?![a-zA-Z@])')


def _active_tex_conditional_matches(line):
    return _active_pattern_matches(_TEX_CONDITIONAL_PATTERN, line)


def _first_active_iffalse_match(line):
    for match in _active_tex_conditional_matches(line):
        if match.group(1) == 'iffalse':
            return match

    return None


def _false_conditional_segments(line, false_conditional_depth):
    segments = []
    active_start = 0
    inactive_start = 0 if false_conditional_depth else None

    for match in _active_tex_conditional_matches(line):
        command = match.group(1)

        if false_conditional_depth:
            if command.startswith('if'):
                false_conditional_depth += 1
            elif command == 'fi':
                false_conditional_depth -= 1
                if false_conditional_depth == 0:
                    segments.append((line[inactive_start:match.end()], False))
                    active_start = match.end()
                    inactive_start = None
            continue

        if command == 'iffalse':
            if active_start < match.start():
                segments.append((line[active_start:match.start()], True))
            false_conditional_depth = 1
            inactive_start = match.start()

    if false_conditional_depth:
        segments.append((line[inactive_start:], False))
    elif active_start < len(line):
        segments.append((line[active_start:], True))

    return segments, false_conditional_depth


def _input_path_candidates(file_path):
    yield file_path

    if not file_path.endswith('.tex'):
        yield f"{file_path}.tex"


_INCLUDE_COMMAND_PATTERN = re.compile(
    r'\\(?P<command>input|include|subfile)(?![A-Za-z@])\s*\{(?P<path>.+?)\}'
    r'|\\(?P<import_command>import|subimport|includefrom|subincludefrom)(?![A-Za-z@])'
    r'\s*\{(?P<import_dir>.+?)\}\s*\{(?P<import_path>.+?)\}'
)


_PRESERVED_SYSTEM_INPUT_NAMES = {
    'epsf',
    'glyphtounicode',
    'insbox',
}


_ARCHIVE_ROOT_INPUT_ALIASES = {
    'basedir',
}


_INPUT_PATH_MACRO_DEFINITION_PATTERNS = [
    re.compile(r'\\(?:newcommand|renewcommand|providecommand)\*?\s*\{\\([A-Za-z@]+)\}\s*\{([^{}#\\]+)\}'),
    re.compile(r'\\def\\([A-Za-z@]+)\s*\{([^{}#\\]+)\}'),
]


def _record_input_path_macros(line, input_path_macros):
    for pattern in _INPUT_PATH_MACRO_DEFINITION_PATTERNS:
        for match in _active_pattern_matches(pattern, line):
            macro_value = match.group(2).strip()
            if macro_value:
                input_path_macros[match.group(1)] = macro_value


def _expand_input_path_macros(input_relative_path, input_path_macros):
    output_parts = []
    current_index = 0

    while current_index < len(input_relative_path):
        current_char = input_relative_path[current_index]
        if current_char != '\\':
            output_parts.append(current_char)
            current_index += 1
            continue

        macro_match = re.match(r'\\([A-Za-z@]+)', input_relative_path[current_index:])
        if not macro_match:
            output_parts.append(current_char)
            current_index += 1
            continue

        macro_name = macro_match.group(1)
        macro_value = input_path_macros.get(macro_name)
        if macro_value is None:
            output_parts.append(macro_match.group(0))
        else:
            output_parts.append(macro_value)

        current_index += len(macro_match.group(0))

    return ''.join(output_parts)


def _normalize_input_relative_path(input_relative_path, input_path_macros):
    expanded_path = _expand_input_path_macros(input_relative_path, input_path_macros)
    return expanded_path.replace('\\', '/')


def _should_preserve_missing_input(input_relative_path):
    if re.search(r'#+[1-9]', input_relative_path):
        return True

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


def _archive_root_relative_input_paths(input_relative_path):
    normalized_path = os.path.normpath(input_relative_path)
    if not os.path.isabs(normalized_path):
        return []

    relative_path = normalized_path.lstrip(os.sep)
    if not relative_path:
        return []

    for alias in _ARCHIVE_ROOT_INPUT_ALIASES:
        alias_prefix = f"{alias}{os.sep}"
        if relative_path.startswith(alias_prefix):
            return [relative_path[len(alias_prefix):]]

    return []


def _input_resolution_candidates(input_relative_path, file_dir, root_dir, source_root_dir):
    candidate_paths = []
    for input_path_candidate in _input_relative_path_candidates(input_relative_path):
        for base_path in _input_base_path_candidates(input_path_candidate, file_dir, root_dir, source_root_dir):
            candidate_paths.extend(_input_path_candidates(base_path))

    return list(_deduplicate_paths(candidate_paths))


def _find_input_file_path(input_relative_path, file_dir, root_dir, source_root_dir):
    for candidate_path in _input_resolution_candidates(input_relative_path, file_dir, root_dir, source_root_dir):
        if os.path.isfile(candidate_path):
            return candidate_path

    return None


def _input_relative_path_candidates(input_relative_path):
    yield input_relative_path

    separator_normalized_path = re.sub(r'\s*/\s*', '/', input_relative_path)
    if separator_normalized_path != input_relative_path:
        yield separator_normalized_path


def _input_base_path_candidates(input_relative_path, file_dir, root_dir, source_root_dir):
    if os.path.isabs(input_relative_path):
        candidate_paths = [os.path.normpath(input_relative_path)]

        for archive_relative_path in _archive_root_relative_input_paths(input_relative_path):
            candidate_paths.extend([
                os.path.normpath(os.path.join(source_root_dir, archive_relative_path)),
                os.path.normpath(os.path.join(root_dir, archive_relative_path)),
            ])

        return list(_deduplicate_paths(candidate_paths))

    return list(_deduplicate_paths([
        os.path.normpath(os.path.join(root_dir, input_relative_path)),
        os.path.normpath(os.path.join(source_root_dir, input_relative_path)),
        os.path.normpath(os.path.join(file_dir, input_relative_path)),
    ]))


def _resolve_input_file_path(input_relative_path, file_dir, root_dir, source_root_dir, command_name='input'):
    input_file_path = _find_input_file_path(input_relative_path, file_dir, root_dir, source_root_dir)
    if input_file_path:
        return input_file_path

    if _should_preserve_missing_input(input_relative_path):
        return None

    candidate_paths = _input_resolution_candidates(input_relative_path, file_dir, root_dir, source_root_dir)
    expected_paths = ', '.join(candidate_paths)
    root_display = root_dir or '.'
    raise FileNotFoundError(
        f"Could not resolve \\{command_name}{{{input_relative_path}}} from {file_dir}. "
        f"Expected {expected_paths} relative to {root_display}."
    )


_IF_FILE_EXISTS_PATTERN = re.compile(r'\\IfFileExists(?![A-Za-z@])')


def _skip_horizontal_whitespace(text, index):
    while index < len(text) and text[index].isspace():
        index += 1

    return index


def _parse_brace_group(text, index):
    index = _skip_horizontal_whitespace(text, index)
    if index >= len(text) or text[index] != '{':
        return None

    depth = 1
    content_start = index + 1
    current_index = content_start

    while current_index < len(text):
        current_char = text[current_index]
        if current_char == '{' and not _is_escaped(text, current_index):
            depth += 1
        elif current_char == '}' and not _is_escaped(text, current_index):
            depth -= 1
            if depth == 0:
                return text[content_start:current_index], current_index + 1

        current_index += 1

    return None


def _parse_if_file_exists_command(line, command_end):
    file_group = _parse_brace_group(line, command_end)
    if file_group is None:
        return None

    file_name, next_index = file_group
    true_group = _parse_brace_group(line, next_index)
    if true_group is None:
        return None

    true_branch, next_index = true_group
    false_group = _parse_brace_group(line, next_index)
    if false_group is None:
        return None

    false_branch, command_end = false_group
    return file_name, true_branch, false_branch, command_end


def _process_if_file_exists_commands_in_line(line, include_pattern, file_dir, root_dir, source_root_dir, input_path_macros):
    if_file_exists_matches = list(_active_pattern_matches(_IF_FILE_EXISTS_PATTERN, line))
    if not if_file_exists_matches:
        return line

    line_parts = []
    previous_end = 0
    processed_command = False

    for match in if_file_exists_matches:
        if match.start() < previous_end:
            continue

        parsed_command = _parse_if_file_exists_command(line, match.end())
        if parsed_command is None:
            continue

        prefix = line[previous_end:match.start()]
        line_parts.append(prefix)
        _record_input_path_macros(prefix, input_path_macros)

        file_name, true_branch, false_branch, command_end = parsed_command
        normalized_file_name = _normalize_input_relative_path(file_name.strip(), input_path_macros)
        selected_branch = (
            true_branch
            if _find_input_file_path(normalized_file_name, file_dir, root_dir, source_root_dir)
            else false_branch
        )
        line_parts.append(
            _process_input_commands_in_line(
                selected_branch,
                include_pattern,
                file_dir,
                root_dir,
                source_root_dir,
                input_path_macros,
            )
        )
        previous_end = command_end
        processed_command = True

    if not processed_command:
        return line

    line_parts.append(line[previous_end:])
    return ''.join(line_parts)


def _first_incomplete_if_file_exists_match(line):
    for match in _active_pattern_matches(_IF_FILE_EXISTS_PATTERN, line):
        if _parse_if_file_exists_command(line, match.end()) is None:
            return match

    return None


def _collect_if_file_exists_command(file_lines, start_index, command_start):
    command_text_parts = []

    for end_index in range(start_index, len(file_lines)):
        line = file_lines[end_index]
        if end_index == start_index:
            command_text_parts.append(line[command_start:])
        else:
            command_text_parts.append(line)

        command_text = ''.join(command_text_parts)
        parsed_command = _parse_if_file_exists_command(command_text, len(r'\IfFileExists'))
        if parsed_command is not None:
            return command_text, parsed_command, end_index

    return None


def _text_to_lines(text):
    if not text:
        return []

    return text.splitlines(keepends=True)


def _process_multiline_if_file_exists_command(
    file_lines,
    line_index,
    include_pattern,
    file_dir,
    root_dir,
    source_root_dir,
    input_path_macros,
):
    line = file_lines[line_index]
    match = _first_incomplete_if_file_exists_match(line)
    if not match:
        return None

    collected_command = _collect_if_file_exists_command(file_lines, line_index, match.start())
    if collected_command is None:
        return None

    command_text, parsed_command, end_index = collected_command
    file_name, true_branch, false_branch, command_end = parsed_command

    prefix = line[:match.start()]
    _record_input_path_macros(prefix, input_path_macros)

    normalized_file_name = _normalize_input_relative_path(file_name.strip(), input_path_macros)
    selected_branch = (
        true_branch
        if _find_input_file_path(normalized_file_name, file_dir, root_dir, source_root_dir)
        else false_branch
    )
    suffix = command_text[command_end:]

    output_lines = []
    if prefix:
        output_lines.append(
            _process_input_commands_in_line(
                prefix,
                include_pattern,
                file_dir,
                root_dir,
                source_root_dir,
                input_path_macros,
            )
        )

    output_lines.extend(
        process_input_commands(
            _text_to_lines(selected_branch),
            file_dir,
            root_dir,
            source_root_dir,
            input_path_macros,
        )
    )

    if suffix:
        output_lines.extend(
            process_input_commands(
                _text_to_lines(suffix),
                file_dir,
                root_dir,
                source_root_dir,
                input_path_macros,
            )
        )

    return output_lines, end_index


def _include_match_command_name(match):
    return match.group('command') or match.group('import_command')


def _include_match_relative_path(match, input_path_macros):
    command_name = _include_match_command_name(match)

    if command_name in {'import', 'subimport', 'includefrom', 'subincludefrom'}:
        import_dir = _normalize_input_relative_path(
            match.group('import_dir').strip(),
            input_path_macros,
        )
        import_path = _normalize_input_relative_path(
            match.group('import_path').strip(),
            input_path_macros,
        )
        return os.path.join(import_dir, import_path)

    return _normalize_input_relative_path(match.group('path').strip(), input_path_macros)


def _process_input_commands_in_line(line, include_pattern, file_dir, root_dir, source_root_dir, input_path_macros):
    line = _process_if_file_exists_commands_in_line(
        line,
        include_pattern,
        file_dir,
        root_dir,
        source_root_dir,
        input_path_macros,
    )
    input_matches = list(_active_pattern_matches(include_pattern, line))

    if not input_matches:
        _record_input_path_macros(line, input_path_macros)
        return line

    line_parts = []
    previous_end = 0

    for match in input_matches:
        prefix = line[previous_end:match.start()]
        line_parts.append(prefix)
        _record_input_path_macros(prefix, input_path_macros)

        command_name = _include_match_command_name(match)
        input_relative_path = _include_match_relative_path(match, input_path_macros)
        input_file_path = _resolve_input_file_path(
            input_relative_path,
            file_dir,
            root_dir,
            source_root_dir,
            command_name=command_name,
        )
        if input_file_path is None:
            line_parts.append(match.group(0))
            previous_end = match.end()
            continue

        input_file_dir = os.path.dirname(input_file_path)
        input_file_lines, _ = read_tex_file(input_file_path)

        input_file_content = process_input_commands(
            input_file_lines,
            input_file_dir,
            root_dir,
            source_root_dir,
            input_path_macros,
        )
        line_parts.append(''.join(input_file_content))
        previous_end = match.end()

    suffix = line[previous_end:]
    line_parts.append(suffix)
    _record_input_path_macros(suffix, input_path_macros)
    return ''.join(line_parts)


def _process_input_commands_with_conditionals(
    line,
    include_pattern,
    file_dir,
    root_dir,
    source_root_dir,
    input_path_macros,
    false_conditional_depth,
):
    segments, false_conditional_depth = _false_conditional_segments(line, false_conditional_depth)
    output_parts = []

    for segment, is_active in segments:
        if is_active:
            output_parts.append(
                _process_input_commands_in_line(
                    segment,
                    include_pattern,
                    file_dir,
                    root_dir,
                    source_root_dir,
                    input_path_macros,
                )
            )
        else:
            output_parts.append(segment)

    return ''.join(output_parts), false_conditional_depth


def process_input_commands(file_lines, file_dir, root_dir=None, source_root_dir=None, input_path_macros=None):
    if root_dir is None:
        root_dir = file_dir
    if source_root_dir is None:
        source_root_dir = _find_source_root_dir(root_dir)
    if input_path_macros is None:
        input_path_macros = {}

    include_pattern = _INCLUDE_COMMAND_PATTERN
    output_lines = []
    in_comment_environment = False
    literal_environment = None
    false_conditional_depth = 0

    line_index = 0
    while line_index < len(file_lines):
        line = file_lines[line_index]

        if false_conditional_depth:
            processed_line, false_conditional_depth = _process_input_commands_with_conditionals(
                line,
                include_pattern,
                file_dir,
                root_dir,
                source_root_dir,
                input_path_macros,
                false_conditional_depth,
            )
            output_lines.append(processed_line)
            line_index += 1
            continue

        if in_comment_environment:
            output_lines.append(line)
            if _first_active_environment_match(line, 'end', {'comment'}):
                in_comment_environment = False
            line_index += 1
            continue

        if literal_environment:
            output_lines.append(line)
            if _first_active_environment_match(line, 'end', {literal_environment}):
                literal_environment = None
            line_index += 1
            continue

        comment_begin = _first_active_environment_match(line, 'begin', {'comment'})
        literal_begin = _first_active_environment_match(line, 'begin', _LITERAL_ENVIRONMENTS)
        false_begin = _first_active_iffalse_match(line)

        if false_begin:
            earliest_environment_begin = None
            for environment_begin in (comment_begin, literal_begin):
                if environment_begin and (
                    earliest_environment_begin is None
                    or environment_begin.start() < earliest_environment_begin.start()
                ):
                    earliest_environment_begin = environment_begin

            if earliest_environment_begin is None or false_begin.start() < earliest_environment_begin.start():
                processed_line, false_conditional_depth = _process_input_commands_with_conditionals(
                    line,
                    include_pattern,
                    file_dir,
                    root_dir,
                    source_root_dir,
                    input_path_macros,
                    false_conditional_depth,
                )
                output_lines.append(processed_line)
                line_index += 1
                continue

        if comment_begin and (not literal_begin or comment_begin.start() < literal_begin.start()):
            prefix = line[:comment_begin.start()]
            suffix = line[comment_begin.start():]
            output_lines.append(
                _process_input_commands_in_line(
                    prefix,
                    include_pattern,
                    file_dir,
                    root_dir,
                    source_root_dir,
                    input_path_macros,
                ) + suffix
            )
            if not _first_active_environment_match(line, 'end', {'comment'}, start_after=comment_begin.start()):
                in_comment_environment = True
            line_index += 1
            continue

        if literal_begin:
            prefix = line[:literal_begin.start()]
            suffix = line[literal_begin.start():]
            output_lines.append(
                _process_input_commands_in_line(
                    prefix,
                    include_pattern,
                    file_dir,
                    root_dir,
                    source_root_dir,
                    input_path_macros,
                ) + suffix
            )
            literal_environment = literal_begin.group(1)
            if _first_active_environment_match(line, 'end', {literal_environment}, start_after=literal_begin.start()):
                literal_environment = None
            line_index += 1
            continue

        multiline_if_result = _process_multiline_if_file_exists_command(
            file_lines,
            line_index,
            include_pattern,
            file_dir,
            root_dir,
            source_root_dir,
            input_path_macros,
        )
        if multiline_if_result is not None:
            processed_lines, end_index = multiline_if_result
            output_lines.extend(processed_lines)
            line_index = end_index + 1
            continue

        output_lines.append(
            _process_input_commands_in_line(
                line,
                include_pattern,
                file_dir,
                root_dir,
                source_root_dir,
                input_path_macros,
            )
        )
        line_index += 1

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


def _active_bibliography_style(file_lines):
    bibliography_style_pattern = re.compile(r'\\bibliographystyle\{(.+?)\}')

    for line in file_lines:
        for match in _active_pattern_matches(bibliography_style_pattern, line):
            style = match.group(1).strip()
            if style:
                return style

    return 'plain'


_CITATION_COMMAND_NAMES = [
    'cite',
    'citep',
    'citet',
    'citealp',
    'citealt',
    'citeauthor',
    'citeyear',
    'citeyearpar',
    'citepalias',
    'citetalias',
    'Citep',
    'Citet',
    'shortcite',
    'nocite',
]


def _active_citation_keys(file_lines):
    citation_pattern = re.compile(
        r'\\(?:' + '|'.join(_CITATION_COMMAND_NAMES) + r')\*?\s*(?:\[[^\]]*\]\s*)*\{([^{}]+)\}'
    )
    citation_keys = []

    for line in file_lines:
        for match in _active_pattern_matches(citation_pattern, line):
            for key in match.group(1).split(','):
                key = key.strip()
                if key:
                    citation_keys.append(key)

    return list(_deduplicate_paths(citation_keys))


def _has_biblatex_commands(file_lines):
    biblatex_pattern = re.compile(r'\\(?:addbibresource|printbibliography)\b')

    for line in file_lines:
        if any(_active_pattern_matches(biblatex_pattern, line)):
            return True

    return False


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


def _bibliography_file_paths(bibliography_commands, root_dir, source_root_dir):
    bibliography_file_paths = []

    for command in bibliography_commands:
        for bib_name in command.split(','):
            for bib_path, _work_dir in _bibliography_path_candidates_with_work_dirs(
                bib_name,
                root_dir,
                source_root_dir,
            ):
                if os.path.isfile(bib_path):
                    bibliography_file_paths.append(bib_path)
                    break

    return list(_deduplicate_paths(bibliography_file_paths))


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


def _start_new_subprocess_group_kwargs():
    if os.name == 'posix':
        return {'start_new_session': True}

    return {}


def _terminate_subprocess_group(process):
    if process.poll() is not None:
        return

    try:
        if os.name == 'posix':
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
        process.wait(timeout=LATEXMK_TERMINATION_GRACE_SECONDS)
    except ProcessLookupError:
        return
    except subprocess.TimeoutExpired:
        if os.name == 'posix':
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
        process.wait()


def _run_latexmk_with_timeout(args, cwd):
    process = subprocess.Popen(
        args,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        **_start_new_subprocess_group_kwargs(),
    )

    try:
        stdout, stderr = process.communicate(timeout=LATEXMK_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        _terminate_subprocess_group(process)
        return subprocess.CompletedProcess(args=args, returncode=-signal.SIGTERM, stdout=b'', stderr=b'latexmk timed out')

    return subprocess.CompletedProcess(args=args, returncode=process.returncode, stdout=stdout, stderr=stderr)


def _generate_bbl_file(main_tex_path, source_root_dir, preferred_work_dirs=None):
    any_success = False
    latexmk_pdf_mode_arg = _latexmk_pdf_mode_arg(source_root_dir)

    for work_dir, main_tex_argument in _latexmk_run_candidates(main_tex_path, source_root_dir, preferred_work_dirs):
        args = [
            'latexmk',
            latexmk_pdf_mode_arg,
            '-interaction=nonstopmode',
            '-halt-on-error',
            main_tex_argument,
        ]
        try:
            result = _run_latexmk_with_timeout(args, work_dir)
        except FileNotFoundError:
            print("Warning: latexmk was not found; trying BibTeX fallback.")
            return False

        if result.returncode == 0:
            any_success = True
            break
        if result.returncode == -signal.SIGTERM:
            print(f"Warning: latexmk timed out for {main_tex_path}; trying BibTeX fallback.")
            break

    if not any_success:
        print(f"Warning: latexmk failed for {main_tex_path}; trying BibTeX fallback.")

    return any_success


def _prepend_tex_search_paths(env, variable_name, paths):
    existing_value = env.get(variable_name)
    search_paths = []

    for path in paths:
        if not path:
            continue

        absolute_path = os.path.abspath(path)
        if absolute_path not in search_paths:
            search_paths.append(absolute_path)

    if existing_value:
        search_paths.append(existing_value)
    else:
        search_paths.append('')

    env[variable_name] = os.pathsep.join(search_paths)


def _generate_bbl_file_with_bibtex_fallback(file_lines, main_tex_path, source_root_dir, bibliography_commands):
    if _has_biblatex_commands(file_lines):
        return False

    citation_keys = _active_citation_keys(file_lines)
    if not citation_keys:
        return False

    main_tex_dir = os.path.dirname(main_tex_path)
    bib_file_paths = _bibliography_file_paths(bibliography_commands, main_tex_dir, source_root_dir)
    if not bib_file_paths:
        return False

    bibliography_style = _active_bibliography_style(file_lines)
    aux_base_name = 'arxiv_latex_merger_bibtex_fallback'

    with tempfile.TemporaryDirectory(prefix='arxiv_latex_merger_bibtex_') as temp_dir:
        bibdata_names = []
        for index, bib_file_path in enumerate(bib_file_paths):
            bibdata_name = f'bib_{index}'
            shutil.copyfile(bib_file_path, os.path.join(temp_dir, f'{bibdata_name}.bib'))
            bibdata_names.append(bibdata_name)

        aux_path = os.path.join(temp_dir, f'{aux_base_name}.aux')
        with open(aux_path, 'w', encoding='utf-8') as aux_file:
            aux_file.write('\\relax\n')
            for citation_key in citation_keys:
                aux_file.write(f'\\citation{{{citation_key}}}\n')
            aux_file.write(f'\\bibstyle{{{bibliography_style}}}\n')
            aux_file.write(f'\\bibdata{{{",".join(bibdata_names)}}}\n')

        env = os.environ.copy()
        bibtex_search_dirs = [temp_dir, main_tex_dir, source_root_dir]
        bibtex_search_dirs.extend(os.path.dirname(path) for path in bib_file_paths)
        _prepend_tex_search_paths(env, 'BIBINPUTS', bibtex_search_dirs)
        _prepend_tex_search_paths(env, 'BSTINPUTS', bibtex_search_dirs)

        try:
            result = subprocess.run(
                ['bibtex', aux_base_name],
                cwd=temp_dir,
                check=False,
                capture_output=True,
                env=env,
            )
        except FileNotFoundError:
            print("Warning: bibtex was not found; trying latexmk if available.")
            return False

        if result.returncode != 0:
            print(f"Warning: bibtex generation failed for {main_tex_path}; trying latexmk if available.")
            return False

        fallback_bbl_path = os.path.join(temp_dir, f'{aux_base_name}.bbl')
        if not os.path.isfile(fallback_bbl_path):
            print(f"Warning: bibtex did not produce a .bbl file for {main_tex_path}; trying latexmk if available.")
            return False

        target_bbl_path = _bbl_file_path_candidates(main_tex_path, source_root_dir)[0]
        shutil.copyfile(fallback_bbl_path, target_bbl_path)

    return True


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
            _generate_bbl_file_with_bibtex_fallback(
                file_lines,
                main_tex_path,
                source_root_dir,
                bibliography_commands,
            )
            bbl_file_path = _first_existing_path(bbl_file_paths)
            if not bbl_file_path:
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
