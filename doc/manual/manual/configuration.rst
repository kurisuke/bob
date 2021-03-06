Configuration
=============

When building packages Bob executes the instructions defined by the recipes.
All recipes are located relative to the working directory in the ``recipes``
subdirectory. Recipes are YAML files with a defined structure. The name of the
recipe and the resulting package(s) is derived from the file name by removing
the trailing '.yaml'. To aid further organization of the recipes they may be
put into subdirectories under ``recipes``. The directory name gets part of the
package name with a ``::``-separator.

To minimize repetition of common functionality there is also an optional
``classes`` subdirectory.  Classes have the same structure as recipes and can
be included from recipes and other classes to factor out common stuff. Files
that do not have the '.yaml' extension are ignored when parsing the recipes and
classes directories.

There are two additional configuration files: ``config.yaml`` and
``default.yaml``. The former contains static configuration options while the
latter holds some options that can have a default value and might be overridden
on the command line. Putting that all together a typical recipe tree looks like
the following::

    .
    ├── classes
    │   └── make.yaml
    ├── config.yaml
    ├── default.yaml
    └── recipes
        ├── busybox.yaml
        ├── initramfs
        │   ├── inittab
        │   └── rcS
        ├── initramfs.yaml
        ├── linux.yaml
        ├── toolchain
        │   ├── arm-linux-gnueabihf.yaml
        │   ├── make.yaml
        │   └── x86.yaml
        └── vexpress.yaml

Such a recipe and configuration tree is meant to be handled by an SCM. As you
can see in the above tree there is a ``toolchain`` subdirectory in the recipes.
The packages in this directory will be named
``toolchain::arm-linux-gnueabihf``, ``toolchain::make`` and ``toolchain::x86``.
You can also see that there are other files (initramfs/...) that are included
by recipes but are otherwise ignored by Bob.

Principle operation
-------------------

All packages are built by traversing the recipe tree starting from one or more
root recipes. These are recipes that have the ``root`` attribute set to
``True``. There must be at least one root recipe in a project. The tree of
recipes is traversed depth first. While following the dependencies Bob keeps a
local state that consists of the following information:

Environment
    Bob always keeps the full set of variables but only a subset is visible
    when executing the scripts. Initially only the variables defined in
    ``default.yaml`` in the ``environment`` section and whitelisted variables
    named by ``whitelist`` are available. Environment variables can be set at
    various points that are described below in more detail.

Tools
    Tools are aliases for paths to executables. Initially there are no tools.
    They are defined by ``provideTools`` and must be explicitly imported by
    upstream recipes by listing ``tools`` in the ``use`` attribute. Like
    environment variables the tools are kept as key value pairs where the key
    is a string and the value is the executable and library paths that are
    imported when using a tool.

Sandbox
    This defines the root file system and paths that are used to build the
    package.  Unless a sandbox is consumed by listing ``sandbox`` in the
    ``use`` attribute of a dependency the normal host executables are used.
    Sandboxed builds are described in a separate section below.

All of this information is carried as local state when traversing the
dependency tree. Each recipe gets a local copy that is propagated downstream.
Any updates to upstream recipes must be done by explicitly offering the
information with one of the ``provide*`` keywords and the upstream recipe must
consume it by adding the relevant item to the ``use`` attribute of the
dependency.

Step execution
~~~~~~~~~~~~~~

The actual work when building a package is done in the following three steps.
They are Bash scripts that are executed with (and only with) the declared
environment and tools.

Checkout
    The checkout step is there to fetch the source code or any external input
    of the package. Despite the script defined by ``checkoutScript`` Bob
    supports a number of source code management systems natively. They can be
    listed in ``checkoutSCM`` and are fetched/updated before the
    ``checkoutScript`` is run.

Build
    This is the step where most of the work should be done to build the
    package. The ``buildScript`` receives the result of the checkout step as
    argument ``$1`` and any further dependency whose result is consumed is
    passed in order starting with ``$2``. If no checkout step was provided
    ``$1`` will point to some invalid path.

Package
    Typically the build step will produce a lot of intermediate files (e.g.
    object files). The package step has the responsibility to distill a clean
    result of the package. The ``packageScript`` will receive a single argument
    with the patch to the build step.

Each step of a recipe is executed separately and always in the above order. The
scripts working directory is already where the result is expected. The scripts
should make no assumption about the absolute path or the relative path to other
steps. Only the working directory might be modified.

Environment handling
~~~~~~~~~~~~~~~~~~~~

The available set of environment variables starts only with the ones named
explicitly by ``whitelist`` in ``config.yaml``. The next step is to set all
variables listed in ``environment`` to their configured value. The user might
additionally override or set certain variables from the command line. The
so calculated set of variables is the starting point for each root recipe.

The next steps are repeated for each recipe as the dependency tree is traversed.
A copy of the environment is inherited from the upstream recipe.

1. Any variable defined in ``environment`` is set to the given value.
2. Make a copy of the local environment that is subsequently passed to each
   dependency (named "forwarded environment" thereafter).
3. For each dependency do the following:

   a. Make a dedicated copy of the environment for the dependency.
   b. Set variables given in the ``environment`` attribute of the dependency
      in this copy.
   c. Descent to the dependency recipe with the that environment.
   d. Merge all variables of the ``provideVars`` section of the dependency
      into the local environment if ``environment`` is listed in the ``use``
      attribute of the dependency.
   e. If the ``forward`` attribute of the dependency is ``True`` then any
      merged variable of the previous step is updated in the forwarded
      environment too.

A subset of the resulting local environment can be passed to the three
execution steps. The available variables to the scripts are defined by
{checkout,build,package}Vars. A variable that is consumed in one step is also
set in the following. This means a variable consumed through checkoutVars is
also set during the build and package steps. Likewise, a variable consumed by
buildVars is set in the package step too. The rationale is that all three steps
form a small pipeline. If a step depends on a certain variable then the result
of the following step is already indirectly dependent on this variable. Thus it
can be set during the following step anyway.

A recipe might optionally offer some variables to the upstream recipe with a
``provideVars`` section. The values of these variables might use variable
substitution where the substituted values are coming from the local
environment. The upstream recipe must explicitly consume these provided
variables by adding ``environment`` to the ``use`` attribute of the dependency.

Tool handling
~~~~~~~~~~~~~

Tools are handled very similar to environment variables when being passed in
the recipe dependency tree. Tools are aliases for a package together with a
relative path to the executable(s) and optionally some library paths for shared
libraries. Another recipe using a tool gets the path to the executable(s) added
to its ``$PATH``.

Starting at the root recipe there are no tools. The next steps are repeated
for each recipe as the dependency tree is traversed. A copy of the tool
aliases is inherited from the upstream recipe.

#. Make a copy of the local tool aliases that is subsequently passed to each
   dependency (named "forwarded tools" thereafter).
#. For each dependency do the following:

   a. Descent to the dependency recipe with the forwarded tools
   b. Merge all tools of the ``provideTools`` section of the dependency into
      the local tools if ``tools`` is listed in the ``use`` attribute of the
      dependency.
   c. If the ``forward`` attribute of the dependency is ``True`` then any
      merged tools of the previous step is updated in the forwarded tools too.

While the full set of tools is carried through the dependency tree only a
specified subset of these tools is available when executing the steps of a
recipe.  The available tools are defined by {checkout,build,package}Tools. A
tool that is consumed in one step is also set in the following. This means a
tool consumed through checkoutTools is also available during the build and
package steps. Likewise, a tool consumed by buildTools is available in the
package step too.

To define one or more tools a recipe must include a ``provideTools`` section
that defines the relative execution path and library paths of one or more tool
aliases. These aliases may be picked up by the upstream recipe by having
``tools`` in the ``use`` attribute of the dependency.

Sandbox operation
~~~~~~~~~~~~~~~~~

Unless a sandbox is configured for a recipe the steps are executed directly on
the host. Bob adds any consumed tools to the front of ``$PATH`` and controls
the available environment variables. Apart from this the build result is pretty
much dependent on the installed applications of the host.

By utilizing `user namespaces`_ on Linux Bob is able to execute the package
steps in a tightly controlled and reproducible environment. This is key to
enable binary reproducible builds. The sandbox image itself is also represented
by a recipe in the project.

.. _user namespaces: http://man7.org/linux/man-pages/man7/user_namespaces.7.html

Initially no sandbox is defined. A downstream recipe might offer its built
package as sandbox through ``provideSandbox``. The upstream recipe must define
``sandbox`` in the ``use`` attribute of this dependency to pick it up as
sandbox. This sandbox is effective only for the current recipe. If ``forward``
is additionally set to ``True`` the following dependencies will inherit this
sandbox for their execution.

Inside the sandbox the result of the consumed or inherited sandbox image is
used as root file system. Only direct inputs of the executed step are visible.
Everything except the working directory and ``/tmp`` is mounted read only to
restrict side effects. The sandbox image must provide everything to execute the
steps (including bash). The only component used from the host is the Linux
kernel and indirectly Python because Bob is written in this language.

Recipe and class keywords
-------------------------

{checkout,build,package}Script
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Type: String

This is the bash script that is executed by Bob at the respective stage when
building the Packet. It is strongly recommended to write the script as a
newline preserving block literal. See the following example (note the pipe
symbol on the end of the first line)::

    buildScript: |
        ./configure
        make

The script is subject to file inclusion with the ``$<<path>>`` and
``$<'path'>`` syntax. The files are included relative to the current recipe.
The given ``path`` might be a shell globbing pattern. If multiple files are
matched by ``path`` the files are sorted by name and then concatenated. The
``$<<path>>`` syntax imports the file(s) as is and replaces the escape pattern
with a (possibly temporary) file name which has the same content. Similar to
that, the ``$<'path'>`` syntax includes the file(s) inline as a quoted string.
In any case the strings are fully quoted and *not* subject to any parameter
substitution.

.. note::
   When including files as quoted strings (``$<'path'>`` syntax) they have to
   be UTF-8 encoded.

The scripts of any classes that are inherited which define
a script for the same step are joined in front of this script in the order the
inheritance is specified. The inheritance graph is traversed depth first and
every class is included exactly once.

During execution of the script only the environment variables SHELL, USER,
TERM, HOME and anything that was declared via {checkout,build,package}Vars
are set. The PATH is reset to "/usr/local/bin:/bin:/usr/bin" or whatever was declared
in config.yaml. Any tools that
are consumed by a {checkout,build,package}Tools declaration are added to the
front of PATH. The same holds for ``$LD_LIBRARY_PATH`` with the difference of starting
completely empty.

Additionally the following variables are populated automatically:

* ``BOB_CWD``: The working directory of the current script.
* ``BOB_ALL_PATHS``: An associative array that holds the paths to the results
  of all dependencies indexed by the package name. This includes indirect
  dependencies such as consumed tools or the sandbox too.
* ``BOB_DEP_PATHS``: An associative array of all direct dependencies. This
  array comes in handy if you want to refer to a dependency by name (e.g.
  ``${BOB_DEP_PATHS[libfoo-dev]}``) instead of the position (e.g. ``$2``).
* ``BOB_TOOL_PATHS``: An associative array that holds the execution paths to
  consumed tools indexed by the package name. All these paths are in ``$PATH``.

{checkout,build,package}Tools
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Type: List of strings

This is a list of tools that should be added to ``$PATH`` during the execution
of the respective checkout/build/package script. A tool denotes a folder in an
(indirect) dependency. A tool might declare some library paths that are then
added to ``$LD_LIBRARY_PATH``.  The order of tools in ``$PATH`` and
``$LD_LIBRARY_PATH``  is unspecified.  It is assumed that each tool provides a
separate set of executables so that the order of their inclusion does not
matter.

A tool that is consumed in one step is also set in the following. This means a
tool consumed through checkoutTools is also available during the build and
package steps. Likewise a tool consumed by buildTools is available in the
package step too. The rationale is that all three steps form a small pipeline.
If a step depends on a certain tool then the result of the following step is
already indirectly dependent on this tool. Thus it can be available during the
following step anyway.


{checkout,build,package}Vars
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Type: List of strings

This is a list of environment variables that should be set during the execution
of the checkout/build/package script. This declares the dependency of the
respective step to the named variables. The content of the variables are
computed as described in section TODO.

It is not an error that a variable listed here is unset. This is especially
useful for classes or to implement default behaviour that can be overridden by
the user from the command line. If you expect a variable to be unset it is your
responsibility to handle that case in the script. Every reference to such a
variable should be guarded with ``${VAR-somthing}`` or ``${VAR+something}``.

A variable that is consumed in one step is also set in the following. This
means a variable consumed through checkoutVars is also set during the build
and package steps. Likewise, a variable consumed by buildVars is set in the
package step too. The rationale is that all three steps form a small pipeline.
If a step depends on a certain variable then the result of the following step
is already indirectly dependent on this variable. Thus it can be set during the
following step anyway.


checkoutDeterministic
~~~~~~~~~~~~~~~~~~~~~

Type: Boolean

By default any checkoutScript is considered indeterministic. The rationale is
that extra care must be taken for a script to fetch always the same sources. If
you are sure that the result of the checkout script is always the same you may
set this to ``True``. All checkoutSCMs on the other hand are capable of
determining automatically whether they are determinstic.

If the checkout is deemed deterministic it enables Bob to apply various
optimizations.  It is also the basis for binary artifacts.

checkoutSCM
~~~~~~~~~~~

Type: SCM-Dictionary or List of SCM-Dictionaries

Bob understands several source code management systems natively. On one hand it
enables the usage of dedicated plugins on a Jenkins server. On the other hand
Bob can manage the checkout step workspace much better in the development build
mode.

All SCMs are fetched/updated before the checkoutScript of the package are run.
The checkoutScript should not move or modify the checkoutSCM directories,
though.

If the package consists of a single git module you can specify the SCM directly::

    checkoutSCM:
        scm: git
        url: git://git.kernel.org/pub/scm/network/ethtool/ethtool.git

If the package is built from multiple modules you can give a list of SCMs::

    checkoutSCM:
        -
            scm: git
            url: git://...
            dir: src/foo
        -
            scm: svn
            url: https://...
            dir: src/bar

There are three common (string) attributes in all SCM specifications: ``scm``,
``dir`` and ``if``. By default the SCMs check out to the root of the workspace.
You may specify any relative path in ``dir`` to checkout to this directory.

By using ``if`` you can selectively enable or disable a particular SCM. Only
simple expressions are possible at the moment. TODO

Currently the following ``scm`` values are supported:

=== ============================ =========================================
scm Description                  Additional attributes
=== ============================ =========================================
git `Git`_ project               | ``url``: URL of remote repository
                                 | ``branch``: Branch to check out (optional, default: master)
                                 | ``tag``: Checkout this tag (optional, overrides branch attribute)
                                 | ``commit``: SHA1 commit Id to check out (optional, overrides branch or tag attribute)
svn `Svn`_ repository            | ``url``: URL of SVN module
                                 | ``revision``: Optional revision number (optional)
url While not a real SCM it      | ``url``: File that should be downloaded
    allows to download (and      | ``digestSHA1``: Expected SHA1 digest of the file (optional)
    extract) files/archives.     | ``digestSHA256``: Expected SHA256 digest of the file (optional)
                                 | ``extract``: Extract directive (optional, default: auto)
=== ============================ =========================================

.. _Git: http://git-scm.com/
.. _Svn: http://subversion.apache.org/

The ``git`` SCM requires at least an ``url`` attribute. The URL might be any
valid Git URL. To checkout a branch other than *master* add a ``branch``
attribute with the branch name. To checkout a tag instead of a branch specify
it with ``tag``. You may specify the commit id directly with a ``commit``
attribute too.

.. note:: The default branch of the remote repository is not used. Bob will
   always checkout "master" unless ``branch``, ``tag`` or ``commit`` is given.

The Svn SCM, like git, requires the ``url`` attribute too. If you specify a
numeric ``revision`` Bob considers the SCM as deterministic.

The ``url`` SCM naturally needs an ``url`` attribute. If a SHA digest is given
with ``digestSHA1`` and/or ``digestSHA256`` the downloaded file will be checked
for a matching hash sum. This also makes the URL deterministic for Bob.
Otherwise the URL will be checked in each build for updates. Based on the file
name ending Bob will try to extract the downloaded file. You may prevent this
by setting the ``extract`` attribute to ``no`` or ``False``. If the heuristic
fails the extraction tool may be specified as ``tar``, ``gzip``, ``xz``, ``7z``
or ``zip`` directly.


depends
~~~~~~~

Type: List of Strings or Dependency-Dictionaries

Declares a list of other recipes that this recipe depends on. Each list entry
might either be a single string with the recipe name or a dictionary with more
fine grained settings. See the following example for both formats::

    depends:
        - foo
        -
            name: toolchain
            use: [tools, environment]
            forward: True
        - bar

In the first and third case only the package is named, meaning the build result
of recipe *foo* resp. *bar* is fed as ``$2`` and ``$3`` to the build
script.

In the second case a recipe named *toolchain* is required but instead of using
its result the recipe imports any declared tools and environment variables from *toolchain*.
Additionally, because of the ``forward`` attribute, these imported
tools and variables are not only imported into the current recipe but also
forwarded to the following recipes (*bar*). The following detailed settings are
supported:

+-------------+-----------------+-----------------------------------------------------+
| Name        | Type            | Description                                         |
+=============+=================+=====================================================+
| name        | String          | Required. The name of the required recipe.          |
+-------------+-----------------+-----------------------------------------------------+
| use         | List of strings | List of the results that are used from the package. |
|             |                 | The following values are allowed:                   |
|             |                 |                                                     |
|             |                 | * ``deps``: provided dependencies of the recipe.    |
|             |                 |   These dependencies will be added right after      |
|             |                 |   the current dependency unless the dependency is   |
|             |                 |   already on the list.                              |
|             |                 | * ``environment``: exported environment variables   |
|             |                 |   of the recipe.                                    |
|             |                 | * ``result``: build result of the recipe.           |
|             |                 | * ``tools``: declared build tools of the recipe.    |
|             |                 | * ``sandbox``:  declared sandbox of the recipe.     |
|             |                 |                                                     |
|             |                 | Default: Use the result and dependencies            |
|             |                 | (``[deps, result]``).                               |
+-------------+-----------------+-----------------------------------------------------+
| forward     | Boolean         | If true, the imported environment, tools and        |
|             |                 | sandbox will be forwarded to the dependencies       |
|             |                 | following this one. Otherwise these variables,      |
|             |                 | tools and/or sandbox will only be accessible in the |
|             |                 | current recipe.                                     |
|             |                 |                                                     |
|             |                 | Default: False.                                     |
+-------------+-----------------+-----------------------------------------------------+
| environment | Dictionary      | This clause allows to define or override            |
|             | (String ->      | environment variables for the dependencies.         |
|             | String)         | Example::                                           |
|             |                 |                                                     |
|             |                 |    environment:                                     |
|             |                 |        FOO: value                                   |
|             |                 |        BAR: baz                                     |
|             |                 |                                                     |
+-------------+-----------------+-----------------------------------------------------+

environment
~~~~~~~~~~~

Type: Dictionary (String -> String)

Defines environment variables in the scope of the current recipe. Any inherited
variables of the upstream recipe with the same name are overwritten. All
variables are passed to downstream recipes.

Example::

   environment:
      PKG_VERSION: "1.2.3"

inherit
~~~~~~~

Type: List of Strings

Include classes with the given name into the current recipe. Example::

   inherit: [cmake]

Classes are searched in the ``classes/`` directory with the given name. The
syntax of classes is the same as the recipes. In particular classes can inherit
other classes too. The inheritance graph is traversed depth first and every
class is included exactly once.

All attributes of the class are merged with the attributes of the current
recipe. If the order is important the attributes of the class are put in front
of the respective attributes of the recipe. For example the scripts of the
inherited class of all steps are inserted in front of the scripts of the
current recipe. 


multiPackage
~~~~~~~~~~~~

Type: Dictionary (String -> Recipe)

By utilizing the ``multiPackage`` keyword it is possible to unify multiple
recipes into one. The final package name is derived from the current recipe
name by appending the key under multiPackage separated by a "-". The following
example recipe foo.yaml declares the two packages foo-bar and foo-baz::

   multiPackage:
      bar:
         packageScript: ...
      baz:
         ...

All other keywords in the recipe are threated as an anonymous base class that
is inherited by the defined multiPackage's. That way you can have common parts
to all multiPackage entries and keep just the distinct parts separately.

A typical use case for this feature are recipes for libraries. There are two
packages that are built from a library: a ``-target`` packet that has the
shared libraries needed during runtime and a ``-dev`` packet that has the
header files and other needed files to link with this library.

provideDeps
~~~~~~~~~~~

Type: List of Strings

The ``provideDeps`` keyword receives a list of dependency names. These must be
dependencies of the current recipe, i.e. they must appear in the ``depends``
section. Such dependencies are subsequently injected into the dependency list
of the upstream recipe that has a dependency to this one. This works in a
transitive fashion too, that is provided dependencies of a downstream recipe
are forwarded to the upstream recipe too.

Example::

   depends:
       - common-dev
       - communication-dev
       - config

   ...

   provideDeps: [common-dev, communication-dev]

Bob will make sure that the forwarded dependencies are compatible in the
injected recipe. That is, any duplicates through injected dependencies must
result in the same package being used.


provideTools
~~~~~~~~~~~~

Type: Dictionary (String -> Path | Tool-Dictionary)

The ``provideTools`` keyword defines an arbitrary number of build tools that
may be used by other steps during the build process. In essence the definition
declares a path (and optionally several library paths) under a certain name
that, if consumed, are added to ``$PATH`` (and ``$LD_LIBRARY_PATH``) of
consuming recipes. Example::

   provideTools:
      host-toolchain:
         path: bin
         libs: [ "sysroot/lib/i386-linux-gnu", "sysroot/usr/lib", "sysroot/usr/lib/i386-linux-gnu" ]

The ``path`` attribute is always needed.  The ``libs`` attribute, if present,
must be a list of paths to needed shared libraries. Any path that is specified
must be relative. If the recipe makes use of existing host binaries and wants
to provide them as tool you should create symlinks to the host paths.

If no library paths are present the declaration may be abbreviated by giving
the relative path directly::

   provideTools:
      host-toolchain: bin

provideVars
~~~~~~~~~~~

Type: Dictionary (String -> String)

Declares arbitrary environment variables with values that should be passed to
the upstream recipe. The values of the declared variables are subject to
variable substitution. The substituted values are taken from the current
package environment. Example::

    provideVars:
        ARCH: "arm"
        CROSS_COMPILE: "arm-linux-${ABI}-"


By default these provided variables are not picked up by upstream recipes. This
must be declared explicitly by a ``use: [environment]`` attribute in the
dependency section of the upstream recipe. Only then are the provided variables
merged into the upstream recipes environment.

provideSandbox
~~~~~~~~~~~~~~

Type: Sandbox-Dictionary

The ``provideSandbox`` keyword offers the current recipe as sandbox for the
upstream recipe. Any consuming upstream recipe (via ``use: [sandbox]``) will
be built in a sandbox where the root file system is the result of the current
recipe. The initial ``$PATH`` is defined with the required ``paths`` keyword
that should hold a list of paths. This will completely replace ``$PATH`` of
the host for consuming recipes.

Optionally there can be a ``mount`` keyword. With ``mount`` it is possible to
specify additional paths of the host that are mounted read only in the sandbox.
The paths are specified as a list of either strings or lists of two elements.
Use a simple string when host and sandbox path are the same. To specify
distinct paths use a list with two entries where the host path is the first
element and the second element is the path in the sandbox.  Variable
substitution is possible for these paths. Example::

    provideSandbox:
        paths: ["/bin", "/usr/bin"]
        mount:
            - "/etc/resolv.conf"
            - "$HOME/.ssh"
            - ["/", "/mnt/host"]

The example can use ``$HOME`` because it is whitelisted by default. Otherwise
any used variable must be defined somewhere or explicitly whitelisted.

.. note::
    The mount paths are considered invariants of the build. That is changing the
    mounts will neither automatically cause a rebuild of the sandbox (and affected
    packages) nor will binary artifacts be re-fetched.

root
~~~~

Type: Boolean

Recipe attribute which defaults to False. If set to True the recipe is declared
a root recipe and becomes a top level package. There must be at least one root
package in a project.

shared
~~~~~~

Type: Boolean

Marking a recipe as shared implies that the result may be shared between
different projects or workspaces. Only completely deterministic packages may be
marked as such. Typically large static packages (such as toolchains) are
enabled as shared packages. By reusing the result the hard disk usage can be
sometimes reduced drastically.

The exact behaviour depends on the build backend. Currently the setting has no
influence on local builds. On Jenkins the result will be copied to a separate
directory in the Jenkins installation and will be used from there. This reduces
the job workspace size considerably at the expense of having artifacts outside
of Jenkins's regular control.

config.yaml
-----------

The file ``config.yaml`` holds all static configuration options that are not
subject to be changed when building packages. The following sections describe
the top level keys that are currently understood. The file is optional or could
be empty.

bobMinimumVersion
~~~~~~~~~~~~~~~~~

Type: String

Defines the minimum required version of Bob that is needed to build this
project. Any older version will refuse to build the project. The version number
given here might be any prefix of the actual version number, e.g. "0.1" instead
of the actual version number (e.g. "0.1.42"). Bob's version number is specified
according to `Semantic Versioning`_. Therefore it is usually only needed to
specify the major and minor version.

.. _Semantic Versioning: http://semver.org/

.. _configuration-config-plugins:

plugins
~~~~~~~

Type: List of strings

Plugins are loaded in the same order as listed here. For each name in this
section there must be a .py-file in the ``plugins`` directory next to the
recipes. For a detailed description of plugins see :ref:`extending-plugins`.

default.yaml
------------

The ``default.yaml`` file holds configuration options that may be overridden by
the user.

environment
~~~~~~~~~~~

Type: Dictionary (String -> String)

Specifies default environment variables. Example::

   environment:
      # Number of make jobs is determined by the number of available processors
      # (nproc).  If desired it can be set to a specific number, e.g. "2". See
      # classes/make.yaml for details.
      MAKE_JOBS: "nproc"

whitelist
~~~~~~~~~

Type: List of Strings

Specifies a list of environment variable keys that should be passed unchanged
to all scripts during execution. The content of these variables are considered
invariants of the build. It is no error if any variable specified in this list
is not set. By default the following environment variables are passed to all
scripts: ``TERM``, ``SHELL``, ``USER`` and ``HOME``. The names given with
``whitelist`` are *added* to the list and does not replace the default list.

Example::

   # Keep ssh-agent working
   whitelist: ["SSH_AGENT_PID", "SSH_AUTH_SOCK"]

archive
~~~~~~~

Type: Dictionary

The ``archive`` key configures the default binary artifact server that should
be used. At least the ``backend`` key must be specified. Any further keys are
specific to the actual backend. See the following table for supported backends
and their configuration.

=========== ==================================================================
Backend     Description
=========== ==================================================================
none        Do not use a binary repository (default).
file        Use a local directory as binary artifact repository. The directory
            is specified in the ``path`` key as absolute path.
http        Uses a HTTP server as binary artifact repository. The server has to
            support the HEAD, PUT and GET methods. The base URL is given in the
            ``url`` key.
=========== ==================================================================

.. warning::
   The usage of binary artifact repositories is still experimental. Use with
   care.

Example::

   archive:
      backend: http
      url: "http://localhost:8001/upload"

