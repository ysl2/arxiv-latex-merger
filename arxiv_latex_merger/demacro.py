r"""
Attempt at writing a de-macroing parser for arXiv merged file.

Functionality:
- Currently replacing 
    \def, 
    \<new|renew|provide]command, 
    \DeclareMathOperator, 
    \DeclareRobustCommand

Known Bugs:
- Cannot de-macro \let macros, although it is assumed that these are not widely used for papers
- Usage of parentheses to specify arguments at instance-level cannot be handled
- No bracket for arguments if \newcommand (or similar) was defined with #1 instead of {#1} cannot be handled

    Work-arounds:
    - In my tests, fixing the files could be possible with some manual prep once you see what breaks

"""

import re
from .utils import find_matching_bracket
from .transforms import newcommand, defcommand
import json
import os

class LatexDemacro:

    def __init__(self, inp, out="./demacro-output.tex"):
        self.fname = inp
        self.inp = LatexDemacro.open_file(inp)
        self.out = out

        # the working version of the input, will be changing throughout processing
        # as lines are removed and transformed
        self.tmp = self.inp

        self.newcommand_declarations = {}
        self.newcommand_transformations = {}
        self.newcommand_occurence_positions = {} # needed to handle transformations, e.g. to avoid swapping \multi in \multicultural

        self.def_declarations = {}
        self.def_transformations = {}
        self.def_occurence_positions = {} # needed to handle transformations, e.g. to avoid swapping \multi in \multicultural

        # error catching
        self.num_exceptions = 0

    def open_file(inp):
        with open(inp, 'r') as file:
            content = file.read()
            return content


    def save_file(self):
        with open(self.out, 'w') as file:
            file.write(self.tmp)


    def remove_comments(self):
        pattern = r'^%.*$[\r\n]*'
        self.tmp = re.sub(pattern, '', self.tmp, flags=re.MULTILINE)


    def remove_def_declarations(self):
        for _, declaration_params in self.def_declarations.items():
            self.tmp = self.tmp.replace(declaration_params['declaration_expression'], "")


    def remove_newcommand_declarations(self):
        for _, declaration_params in self.newcommand_declarations.items():
            self.tmp = self.tmp.replace(declaration_params['declaration_expression'], "")

    def _next_nonspace_index(self, start):
        while start < len(self.tmp) and self.tmp[start].isspace():
            start += 1
        return start

    def _read_control_sequence_argument(self, start):
        start = self._next_nonspace_index(start)
        if start >= len(self.tmp):
            return None, start

        if self.tmp[start] != "\\":
            end = start
            while end < len(self.tmp) and not self.tmp[end].isspace():
                end += 1
            return self.tmp[start:end], end

        end = start + 1
        if end < len(self.tmp) and self.tmp[end].isalpha():
            while end < len(self.tmp) and self.tmp[end].isalpha():
                end += 1
        elif end < len(self.tmp):
            end += 1

        return self.tmp[start:end], end


    def parse_def_declarations(self):

        # look for def command
        def_pattern = re.compile(r'\\def\s*\\([a-zA-Z@:\#\']+|.)\s*')
        defs = def_pattern.finditer(self.tmp)
        for match in defs:
            name = match.group(1)
            num_args = 0
            definition = None
            declaration_start = match.start()
            start = match.end()

            # looking for argument declarations in parentheses or square brackets
            start = self._next_nonspace_index(start)
            if start < len(self.tmp) and self.tmp[start] in "([":
                bra = self.tmp[start]
                end = find_matching_bracket(self.tmp, start, bra=bra)
                if end == -1:
                    continue
                def_args = self.tmp[start + 1:end]
                num_args = len(def_args.split("#")[1:])
                start = end + 1

            # looking for definition
            start = self._next_nonspace_index(start)
            if start < len(self.tmp) and self.tmp[start] == "{":
                end = find_matching_bracket(self.tmp, start, bra="{")
                if end == -1:
                    continue
                definition = self.tmp[start + 1:end]
                if "\n" not in definition:
                    definition = definition.strip()
                declaration_end = end
            else:
                continue

            self.def_declarations[name] = {'num_args': num_args,
                                    'definition': definition,
                                    'declaration_expression': self.tmp[declaration_start : declaration_end + 1]}
        

    def parse_newcommand_declarations(self):

        # look for [renew|new|provide]*command
        # TODO: Generalize the \name regex pattern
        newcommand_pattern = re.compile(r'\\(?:DeclareMathOperator|DeclareRobustCommand|renewcommand|newcommand|providecommand)+\*?{?\\(\w+)}?')
        newcommands = newcommand_pattern.finditer(self.tmp)
        for match in newcommands:
            name = match.group(1)
            num_args = 0
            default = None
            definition = None
            declaration_start = match.start()
            start = match.end()
            end = find_matching_bracket(self.tmp, start, bra="[")
            if end != -1:
                num_args = int(self.tmp[start + 1:end])
                
                # look for default
                start = end + 1
                end = find_matching_bracket(self.tmp, start, bra="[")
                if end != -1:
                    default = self.tmp[start + 1:end]

                    # look for definition after default
                    start = end+1
                    end = find_matching_bracket(self.tmp, start, bra="{")
                    if end != -1:
                        definition = self.tmp[start + 1:end]

                # look for definition without default
                if not default:
                    end = find_matching_bracket(self.tmp, start, bra="{")
                    if end != -1:
                        definition = self.tmp[start + 1:end]
            
            # look for definition without num_args
            end = find_matching_bracket(self.tmp, start, bra="{")
            if end != -1:
                definition = self.tmp[start + 1:end]
            
            declaration_end = end
            
            self.newcommand_declarations[name] = {'num_args': num_args,
                                    'default': default,
                                    'definition': definition,
                                    'declaration_expression': self.tmp[declaration_start : declaration_end + 1]}
        # print("\n"*5)


    def parse_for_def_occurences(self):
        for macro in self.def_declarations.keys():
            # work-around for re-definitions of existing symbol characters
            if macro.isalnum():
                macro_pattern = re.compile(rf'\\{re.escape(macro)}\b')
            else:
                macro_pattern = re.compile(rf'\\{re.escape(macro)}')
            macro_occurences = macro_pattern.finditer(self.tmp)
            for match in macro_occurences:
                occurence = match.group(0)
                # print(occurence)
                num_args = self.def_declarations[macro]['num_args']
                args = []
                
                if num_args>0:
                    start = match.end()
                    token_start = self._next_nonspace_index(start)
                    end = -1
                    if token_start < len(self.tmp) and self.tmp[token_start] == "(":
                        end = find_matching_bracket(self.tmp, token_start, bra="(")
                        if end != -1:
                            for arg in self.tmp[token_start + 1:end].split(","):
                                args.append(arg.strip())
                            occurence = occurence + "(" + self.tmp[token_start + 1:end] + ")"
                    
                    token_start = self._next_nonspace_index(start)
                    if not args and token_start < len(self.tmp) and self.tmp[token_start] == "[":
                        end = find_matching_bracket(self.tmp, token_start, bra="[")
                        if end != -1:
                            for arg in self.tmp[token_start + 1:end].split(","):
                                args.append(arg.strip())
                            occurence = occurence + "[" + self.tmp[token_start + 1:end] + "]"

                    if not args and num_args == 1:
                        arg, end = self._read_control_sequence_argument(start)
                        if arg:
                            args.append(arg)
                            occurence = occurence + arg
                
                try:
                    self.def_transformations[occurence] = defcommand(self.def_declarations[macro]['definition'],
                                                                    num_args,
                                                                    args)
                    self.def_occurence_positions[occurence] = {'start': match.start(), 'end': match.start()+len(occurence)}                    
                except:
                    if occurence in self.newcommand_transformations:
                        self.newcommand_transformations.pop(occurence)
                    if occurence in self.newcommand_occurence_positions:
                        self.newcommand_occurence_positions.pop(occurence)
                    print(f"Could not define def transformation for macro {macro} in the expression...")
                    self.num_exceptions += 1
                    continue

    def parse_for_newcommand_occurences(self):
        for macro in self.newcommand_declarations.keys():
            # print(macro)
            # TODO: work-around needed here? for definitions of symbol characters?
            macro_pattern = re.compile(rf'\\{macro}\b')
            macro_occurences = macro_pattern.finditer(self.tmp)
            # print("macro:", macro)
            for match in macro_occurences:
                occurence = match.group(0)
                num_args = self.newcommand_declarations[macro]['num_args']
                default = self.newcommand_declarations[macro]['default']
                args = []
                end = None

                if num_args>0:
                    # macro has arguments
                    # print(f"looking at macro with {self.declarations[macro]['num_args']} arguments")
                    
                    # BUG: this currently fails for macros that are defined
                    # without {#1}..., as they do not require braces for the argument calls
                    # this may be hard to implement
                    
                    if default:
                        # print(f"looking at macro with {self.declarations[macro]['default']} default")
                        # look for optional re-definition of default
                        start = match.end()
                        end = find_matching_bracket(self.tmp, start, bra="[")
                        if end != -1:
                            default = self.tmp[start + 1:end]
                            occurence = occurence + "[" + default + "]"

                            # look for 2nd arg
                            start = end + 1
                            end = find_matching_bracket(self.tmp, start, bra="{")
                            if end != -1:
                                args.append(self.tmp[start + 1:end])
                                occurence = occurence + "{" + args[-1] + "}"

                        
                        # no optional default found
                        else:                        
                                
                            # look for 1st argument
                            end = find_matching_bracket(self.tmp, start, bra="{")
                            if end != -1:
                                args.append(self.tmp[start + 1:end])
                                occurence = occurence + "{" + args[-1] + "}"
                        
                    else:
                        # TODO: Loop this for multiple args?
                        # macro doesn't have a default, look for first arg
                        start = match.end()
                        end = find_matching_bracket(self.tmp, start, bra="{")
                        if end != -1:
                            # print(self.tmp[end-5:end])
                            # print(self.tmp[start-5:end+5])
                            args.append(self.tmp[start + 1:end])
                            occurence = occurence + "{" + args[-1] + "}"

                        if num_args>1:
                            # look for second arg
                            start = end+1
                            end = find_matching_bracket(self.tmp, start, bra="{")
                            if end != -1:
                                args.append(self.tmp[start + 1:end])
                                occurence = occurence + "{" + args[-1] + "}"
                        
                        # TODO: some arguments are called with parentheses, how to accommodate? this?
                        # end = find_matching_bracket(self.tmp, start, bra="(")
                        # if end != -1:
                        #     args.append(self.tmp[start + 1:end])
                        #     occurence = occurence + "(" + args[-1] + ")"

                        # if num_args>1:
                        #     # look for second arg
                        #     start = end+1
                        #     end = find_matching_bracket(self.tmp, start, bra="(")
                        #     if end != -1:
                        #         args.append(self.tmp[start + 1:end])
                        #         occurence = occurence + "(" + args[-1] + ")"
                try:
                    # print("occurence:", occurence)
                    # print(args)
                    self.newcommand_transformations[occurence] = newcommand(self.newcommand_declarations[macro]['definition'],
                                                                    num_args,
                                                                    args,
                                                                    default)
                    self.newcommand_occurence_positions[occurence] = {'start': match.start(), 'end': match.start()+len(occurence)}
                    return # stop when you found one
                except:
                    if occurence in self.newcommand_transformations:
                        self.newcommand_transformations.pop(occurence)
                    if occurence in self.newcommand_occurence_positions:
                        self.newcommand_occurence_positions.pop(occurence)
                    print(f"Could not define newcommand transformation for macro {macro} in the expression starting with {self.tmp[match.start():match.start()+15]}...")
                    self.num_exceptions += 1
                    continue
                
    def transform_def_occurences(self):
        while len(self.def_occurence_positions)>0:
            for occurence, demacroed in self.def_transformations.copy().items():
                #BUG: THIS MATCHES \multi IN \multicultural, should be only 0-arg macros
                # self.tmp = self.tmp.replace(occurence, demacroed)
                
                #TODO: attempts to fix, but re.escape messes up everything
                # print(occurence)
                # print(re.escape(occurence))
                # occurence = occurence.replace("\\\\", "\\")
                # self.tmp = re.sub(rf"({re.escape(occurence)})\b", demacroed, self.tmp)
                # self.tmp = re.sub(re.compile(rf"{re.escape(occurence)}\b"), demacroed, self.tmp)

                
                # current solution is with parse_for_newcommand_occurences
                # that breaks upon finding a single occurence for a macro, until all are transformed
                start = self.def_occurence_positions[occurence]['start']
                end = self.def_occurence_positions[occurence]['end']
                self.def_transformations.pop(occurence)
                self.def_occurence_positions.pop(occurence)
                self.tmp = self.tmp[:start] + demacroed + self.tmp[end:]
                if occurence not in demacroed:
                    self.parse_for_def_occurences() # updating indices, since the text has been altered
                # print(json.dumps(self.def_transformations, indent=2))
                # print(json.dumps(self.def_occurence_positions, indent=2))

    def transform_newcommand_occurences(self):
        while len(self.newcommand_transformations)>0:
            for occurence, demacroed in self.newcommand_transformations.copy().items():
                #BUG: THIS MATCHES \multi IN \multicultural, should be only 0-arg macros
                # self.tmp = self.tmp.replace(occurence, demacroed)
                
                #TODO: attempts to fix, but re.escape messes up everything
                # print(occurence)
                # print(re.escape(occurence))
                # occurence = occurence.replace("\\\\", "\\")
                # self.tmp = re.sub(rf"({re.escape(occurence)})\b", demacroed, self.tmp)
                # self.tmp = re.sub(re.compile(rf"{re.escape(occurence)}\b"), demacroed, self.tmp)

                
                # current solution is with parse_for_newcommand_occurences
                # that breaks upon finding a single occurence for a macro, until all are transformed
                start = self.newcommand_occurence_positions[occurence]['start']
                end = self.newcommand_occurence_positions[occurence]['end']
                self.newcommand_transformations.pop(occurence)
                self.newcommand_occurence_positions.pop(occurence)
                self.tmp = self.tmp[:start] + demacroed + self.tmp[end:]
                self.parse_for_newcommand_occurences() # updating indices, since the text has been altered
                # print(json.dumps(self.newcommand_transformations, indent=2))
                # print(json.dumps(self.newcommand_occurence_positions, indent=2))


    def process(self):
        # print(f"Starting to de-macro {self.fname}...")
        self.remove_comments()

        self.parse_def_declarations()
        self.remove_def_declarations()
        self.parse_for_def_occurences()
        # print(json.dumps(self.def_declarations, indent=2))
        self.transform_def_occurences()

        self.parse_newcommand_declarations()
        self.remove_newcommand_declarations()
        self.parse_for_newcommand_occurences()
        # print(json.dumps(self.newcommand_declarations, indent=2))
        self.transform_newcommand_occurences()

        # print(f"Caught {self.num_exceptions} exceptions while de-macroing...")

        return self.tmp


if __name__=="__main__":
    c = LatexDemacro("./demacro-input.tex")
    c.process()
    c.save_file()
    print(c.tmp)
