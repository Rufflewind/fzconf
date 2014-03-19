#!/usr/bin/env python
# Simple build system for generating makefiles.
import os, re, subprocess, sys

_cxxflag_cache = {}

def command_exists(command):
    '''Check if the (shell) command exists by executing it.'''
    try:
        with open(os.devnull, "w") as devnull:
            subprocess.call([command], stdout=devnull, stderr=devnull)
        return True
    except OSError:
        return False

def try_compile(command, lang, code=""):
    '''Attempts to compile the given code.  Be sure to specify a language using
    the `-x LANG` switch.'''
    try:
        with open(os.devnull, "w") as devnull:
            tmpfile = "~~"
            proc = subprocess.Popen(command + ["-x", lang,
                                               "-o", tmpfile, "-c", "-"],
                                    stdin=subprocess.PIPE,
                                    stdout=devnull, stderr=devnull)
            proc.communicate(code)
            os.remove(tmpfile)
            return proc.returncode == 0
    except OSError:
        return False

def cxxflags_check(compiler, flags):
    '''Checks (and converts, if appropriate) the compiler flags for
    compatibility with the current system.'''
    new_flags = []
    try:
        cache = _cxxflag_cache[compiler]
    except KeyError:
        cache = {}
        _cxxflag_cache[compiler] = cache
    for flag in flags:
        try:
            flag = cache[flag]
            if flag:
                new_flags.append(flag)
            continue
        except KeyError:
            pass
        old_flag = flag

        if flag == "-std=c++11":
            if not try_compile([compiler, flag], "c++"):
                flag = "-std=c++0x"
                if not try_compile([compiler, flag], "c++"):
                    flag = ""
                    sys.stderr.write("** Warning: no C++11 support.\n")
                    cache["-std=c++0x"] = flag
                else:
                    sys.stderr.write("** Warning: limited C++11 support.\n")
                cache[old_flag] = flag

        if flag == "-std=c++0x":
            if not try_compile([compiler, flag], "c++"):
                flag = ""
                sys.stderr.write("** Warning: no C++11 support.\n")
                cache[old_flag] = flag

        elif flag.startswith("-ferror-limit="):
            if compiler != "clang++":
                flag = ""
                cache[old_flag] = flag

        if flag:
            new_flags.append(flag)
    return new_flags

class Args(object):
    '''Simple command-line argument parser.'''
    def __init__(self, args=None):
        '''Initializes the parser from the given list of arguments (defaults
        to `sys.argv[1:]`).
        '''
        self._index = 0
        self._args = args or sys.argv[1:]
    def next(self):
        '''Gets the next argument, returning its name and value as strings.  A
        `StopIteration` error is raised if there are no more arguments.
        '''
        try:
            arg = self._args[self._index]
            self._index += 1
            if "=" in arg:
                return arg.split("=", 2)
            return arg, ""
        except IndexError:
            return StopIteration()
    def __bool__(self):
        '''Determines whether there are any more arguments left.'''
        return self._index < len(self._args)
    __nonzero__ = __bool__

class Project(object):
    def __init__(self, name, *inputs, **arguments):
        '''Creates a project.  Certains macros may be needed for the makefile
        to work correctly depending on the project type, e.g. `CXX`,
        `CXXFLAGS`, `PCHEXT`, `OUTDIR`, `INTDIR`, etc.

        @param name          Name of the final output file.
        @param inputs        Input files (e.g. `*.c` or `*.cpp` sources).
                             Dependent header files are automatically searched
                             and thus do not need to be included.
        @param outdir        Output directory (default: `"$(OUTDIR)"`).
        @param outname       Output filename w/out dirname (default: `name`)
        @param intdir        Directory to store intermediate files (default:
                             `"$(INTDIR)"`).
        @param lang          Language of the project (default: autodetect).
                             Supported languages:
                                 `"c"`
                                 `"c++"`
                                 `"c-header"` (precompiled header)
                                 `"c++-header"` (precompiled header)
        @param postbuild     List of commands done after building.
        @param postexec      List of commands done after `postbuild` (similar
                             to `postbuild` but these are *always* run if the
                             `name` is different from the output name).
        @param extdeps       List of external (non-generated) dependencies.
        @param outs          Additional output files to be cleaned up.
        @param ...flags      List of additional parameters given to the
                             compiler.  The placeholder `...` can be either `c`
                             `cpp`, `cxx`, or `link`.  Linker flags are always
                             placed at the end of the command.
        @param precompiled   Name of the precompiled header project to be
                             included.  Be sure to include a `PCHEXT` macro
                             that defines the correct file extension.  Note
                             that C code will not be compiled with it.
        '''
        self.name = name
        self.inputs = inputs
        self.outdir = arguments.get("outdir", "$(OUTDIR)")
        self.outname = arguments.get("outname", self.name)
        self.intdir = arguments.get("intdir", "$(INTDIR)")
        self.lang = arguments.get("lang", None)
        self.postbuild = arguments.get("postbuild", ())
        self.postexec = arguments.get("postexec", ())
        self.extdeps = arguments.get("extdeps", [])
        self.rules = {}
        self.outs = set(arguments.get("outs", ()))
        self.phonys = set()
        self.precompiled = arguments.get("precompiled", "")
        self.linkflags = arguments.get("linkflags", [])
        self.cppflags = arguments.get("cppflags", [])
        self.cc = ["$(CC)", "$(CPPFLAGS)"]
        self.cc.extend(self.cppflags)
        self.cc.append("$(CFLAGS)")
        self.cc.extend(arguments.get("cflags", []))
        self.cxx = ["$(CXX)", "$(CPPFLAGS)"]
        self.cxx.extend(self.cppflags)
        self.cxx.append("$(CXXFLAGS)")
        self.cxx.extend(arguments.get("cxxflags", []))
        if self.precompiled:
            self.cxx.extend(["-include", "$(INTDIR)" + self.precompiled])
            self.extdeps.append("$(INTDIR)"  + self.precompiled + "$(PCHEXT)")

        # This variable stores the relative path of the final output file.
        # (Modify this as needed if the output file is different.)
        out = self.outdir + self.outname

        # Generate precompiled header
        if self.lang == "c-header" or self.lang == "c++-header":
            # This rule generates a header file that includes all the inputs
            cmds = [("test", self.outdir, "&&", "mkdir", "-p", self.outdir),
                    ("rm", "-f", out)]
            deps = set()
            for inp in self.inputs:
                if inp.startswith("<") and inp.endswith(">"):
                    cmds.append("echo >>" + out +
                                " '#include " + inp + "'")
                else:
                    deps.update(self._find_deps_cpp(inp))
                    cmds.append("echo >>" + out +
                                " '#include \"" + inp + "\"'")
            self.rules[out] = (deps, cmds)
            self.outs.add(out)
            out_pch = out + "$(PCHEXT)"
            self.rules[out_pch] = (
                [out],
                [{"c-header": self.cc, "c++-header": self.cxx}[self.lang]
                 + ["-o", "$@", "-x", self.lang, "-c", out]]
            )
            self.outs.add(out_pch)
            out = out_pch

        # Generic projects
        else:

            # Compilation stage (note that we don't necessarily know what
            # language the project is in yet)
            self.ints = set()
            for inp in self.inputs:
                _, ext = os.path.splitext(inp)

                # Compile C++
                if ext in (".cc", ".cpp", ".cxx", ".c++"):
                    if not self.lang or self.lang == "c":
                        self.lang = "c++"
                    elif self.lang != "c++":
                        raise Exception("Mixing '" + self.lang +
                                        "' with 'c++'.")
                    self._proc_intermediate(self.cxx, inp)

                # Compile C
                elif ext in (".c",):
                    if not self.lang:
                        self.lang = "c"
                    elif self.lang != "c" and self.lang != "c++":
                        raise Exception("Mixing '" + self.lang +
                                        "' with 'c'.")
                    self._proc_intermediate(self.cc, inp)

                # Future extensions: don't forget to add the 'extdeps'!

                else:
                    raise Exception("Unrecognized extension: " + ext)

            # Empty (custom) project
            if not self.lang:
                self.rules[out] = ([], [])
                self.phonys.add(out)

            # Link C++
            elif self.lang == "c++":
                self._proc_linkage(self.cxx, out)

            # Link C
            elif self.lang == "c":
                self._proc_linkage(self.cc, out)

            else:
                raise Exception("Unrecognized language: " + self.lang)

        # Add a phony alias rule if needed
        if self.name != out:
            self.rules[self.name] = ([out], [])
            self.phonys.add(self.name)

        # Add post-build & post-exec commands
        self.rules[out][1].extend(self.postbuild)
        self.rules[self.name][1].extend(self.postexec)

    def _proc_intermediate(self, compiler, inp):
        intfn = self.intdir + inp + ".o"
        if intfn not in self.rules:
            deps = self._find_deps_cpp(inp)
            deps.update(self.extdeps)
            self.rules[intfn] = (
                deps,
                [("mkdir", "-p", "`dirname $@`"),
                 compiler + ["-o", "$@", "-c", inp]]
            )
            self.ints.add(intfn)

    def _proc_linkage(self, compiler, out):
        deps = self.ints.copy()
        deps.update(self.extdeps)
        self.rules[out] = (
            deps,
            [("mkdir", "-p", "`dirname $@`"),
             # Remove the "-std=" flag when linking
             [f for f in compiler if not f.startswith("-std=")] +
             ["-o", "$@"] + sorted(list(self.ints)) + self.linkflags]
        )
        self.outs.add(out)
        self.outs.update(self.ints)

    def _find_deps_cpp(self, filename):
        '''Recursively finds all `#include` dependencies of a C/C++ file.
        Library includes (i.e. with '< >' brackets) are ignored.  The
        dependencies are returned as a `set`.  (The given file is considered
        to be a dependency of itself and thus included in the results.)
        '''
        regex = "[\\t ]*#[\\t ]*include[\\t ]*\"([^\"]+)\""
        queue = [filename]
        deps = set(queue)
        while queue:
            path = queue.pop()
            workdir = os.path.abspath(os.path.dirname(path))
            try:
                with open(path, "rt") as f:
                    for line in f:
                        m = re.match(regex, line)
                        if m:
                            path = m.group(1)
                            path = os.path.relpath(os.path.join(workdir, path))
                            if path not in deps:
                                deps.add(path)
                                queue.append(path)
            except IOError:
                sys.stderr.write("** Warning: can't open: '" + path + "'\n")
        return deps

class Makefile(object):

    def __init__(self, macros=None, rules=None, cleans=None, phonys=None):
        '''Creates a makefile.
        @param macros  Format: {name: value, ...}
        @param rules   Format: {target: ([prereq, ...], [command, ...]), ...}
        @param cleans  A `set` of files that should be deleted by 'make clean'.
        @param phonys  A `set` of phony targets.
        '''
        self.macros = macros or {}
        self.rules = rules or {}
        self.cleans = cleans or set()
        self.phonys = phonys or set(
            ["all", "build", "clean", "install", "test"])
        self.first_target = None
        self.silent = None              # Either `None` or a `set`
        self.suffixes = set()           # Either `None` or a `set`

    def import_projects(self, *projects):
        '''Imports the projects into the makefile.  When this method is called
        for the first name with non-empty arguments, the first project in the
        list will become the default target.
        '''
        if not self.first_target and projects:
            self.first_target = projects[0].name
        for project in projects:
            self.rules.update(project.rules)
            self.cleans.update(project.outs)
            self.phonys.update(project.phonys)

    def save(self, filename="Makefile"):
        '''Saves the makefile to an actual file.  If a file already exists, it
        will be overwritten.
        '''
        # Common header section
        header = \
            "#!/usr/bin/env make\n" + \
            "# Autogenerated by the 'configure' script.\n" + \
            "MAKEFLAGS+=--no-builtin-rules"

        # Define macros
        macros = []
        for name, value in sorted(self.macros.items()):
            if isinstance(value, str):
                macros.append(name + "=" + value)
            else:
                macros.append(name + "=" + " ".join(value))
        macros = "\n".join(macros)

        # Handle some special rules
        first = self.first_target
        rules = []
        if first:
            rules.append((first, self.rules[first]))
        rules += sorted([(k, v) for k, v in self.rules.items() if k != first])
        if self.cleans:
            rules.append(("clean", ([], [["rm", "-rf"] +
                                         sorted(list(self.cleans))])))
        if self.phonys:
            rules.append((".PHONY", (sorted(list(self.phonys)), [])))
        if isinstance(self.silent, list):
            rules.append((".SILENT", (sorted(list(self.silent)), [])))
        if isinstance(self.suffixes, list):
            rules.append((".SUFFIXES", (sorted(list(self.suffixes)), [])))

        # Define the rules
        blocks = [header, macros]
        for target, (prerequisites, commands) in rules:
            rule = [target +
                    (": " + " ".join(sorted(list(prerequisites)))).rstrip()]
            for command in commands:
                if isinstance(command, str):
                    rule.append("\t" + command)
                else:
                    rule.append("\t" + " ".join(command))
            blocks.append("\n".join(rule))

        # Finally, write to the file
        with open(filename, "wt") as mf:
            mf.write("\n\n".join(blocks))
            mf.write("\n")
