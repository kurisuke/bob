# Bob build tool
# Copyright (C) 2016  TechniSat Digital GmbH
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

from ..errors import BuildError
from ..input import walkPackagePath
from ..state import BobState
from ..utils import asHexStr, colorize, hashDirectory, hashFile, removePath, emptyDirectory
from datetime import datetime
from glob import glob
from pipes import quote
from tempfile import TemporaryDirectory, TemporaryFile
import argparse
import datetime
import os
import shutil
import stat
import subprocess
import tarfile
import urllib.request, urllib.error

# Output verbosity:
#    <= -2: package name
#    == -1: package name, package steps
#    ==  0: package name, package steps, stderr
#    ==  1: package name, package steps, stderr, stdout
#    ==  2: package name, package steps, stderr, stdout, set -x

class DummyArchive:
    def uploadPackage(self, buildId, path):
        pass

    def downloadPackage(self, buildId, path):
        return False

class LocalArchive:
    def __init__(self, spec):
        self.__basePath = os.path.abspath(spec["path"])

    def uploadPackage(self, buildId, path):
        packageResultId = asHexStr(buildId)
        packageResultPath = os.path.join(self.__basePath, packageResultId[0:2],
                                         packageResultId[2:4])
        packageResultFile = os.path.join(packageResultPath,
                                         packageResultId[4:]) + ".tgz"
        if os.path.isfile(packageResultFile):
            print("   UPLOAD    skipped ({} exists in archive)".format(path))
            return

        print(colorize("   UPLOAD    {}".format(path), "32"))
        if not os.path.isdir(packageResultPath): os.makedirs(packageResultPath)
        with tarfile.open(packageResultFile, "w:gz") as tar:
            tar.add(path, arcname=".")

    def downloadPackage(self, buildId, path):
        print(colorize("   DOWNLOAD  {}...".format(path), "32"), end="")
        packageResultId = asHexStr(buildId)
        packageResultPath = os.path.join(self.__basePath, packageResultId[0:2],
                                         packageResultId[2:4])
        packageResultFile = os.path.join(packageResultPath,
                                         packageResultId[4:]) + ".tgz"
        if os.path.isfile(packageResultFile):
            removePath(path)
            os.makedirs(path)
            with tarfile.open(packageResultFile, "r:gz") as tar:
                tar.extractall(path)
            print(colorize("ok", "32"))
            return True
        else:
            print(colorize("not found", "33"))
            return False


class SimpleHttpArchive:
    def __init__(self, spec):
        self.__url = spec["url"]

    def _makeUrl(self, buildId):
        packageResultId = asHexStr(buildId)
        return "/".join([self.__url, packageResultId[0:2], packageResultId[2:4],
            packageResultId[4:] + ".tgz"])

    def uploadPackage(self, buildId, path):
        url = self._makeUrl(buildId)

        # check if already there
        try:
            try:
                req = urllib.request.Request(url=url, method='HEAD')
                f = urllib.request.urlopen(req)
                print("   UPLOAD    skipped ({} exists in archive)".format(path))
                return
            except urllib.error.HTTPError as e:
                if e.code != 404:
                    raise BuildError("Error for HEAD on "+url+": "+e.reason)

            print(colorize("   UPLOAD    {}".format(path), "32"))
            with TemporaryFile() as tmpFile:
                with tarfile.open(fileobj=tmpFile, mode="w:gz") as tar:
                    tar.add(path, arcname=".")
                tmpFile.seek(0)
                req = urllib.request.Request(url=url, data=tmpFile.read(),
                                             method='PUT')
                f = urllib.request.urlopen(req)
        except urllib.error.URLError as e:
            raise BuildError("Error uploading package: "+str(e.reason))

    def downloadPackage(self, buildId, path):
        ret = False
        print(colorize("   DOWNLOAD  {}...".format(path), "32"), end="")
        url = self._makeUrl(buildId)
        try:
            (localFilename, headers) = urllib.request.urlretrieve(url)
            removePath(path)
            os.makedirs(path)
            with tarfile.open(localFilename, "r:gz", errorlevel=1) as tar:
                tar.extractall(path)
            ret = True
            print(colorize("ok", "32"))
        except urllib.error.URLError as e:
            print(colorize(str(e.reason), "33"))
        except OSError as e:
            raise BuildError("Error: " + str(e))
        finally:
            urllib.request.urlcleanup()

        return ret

class LocalBuilder:

    RUN_TEMPLATE = """#!/bin/bash

on_exit()
{{
     if [[ -n "$_sandbox" ]] ; then
          if [[ $_keep_sandbox = 0 ]] ; then
                rm -rf "$_sandbox"
          else
                echo "Keeping sandbox in $_sandbox" >&2
          fi
     fi
}}

run()
{{
    {SANDBOX_CMD} "$@"
}}

run_script()
{{
    local ret=0 trace=""
    if [[ $_verbose -ge 3 ]] ; then trace="-x" ; fi

    echo "### START: `date`"
    run /bin/bash $trace -- ../script {ARGS}
    ret=$?
    echo "### END($ret): `date`"

    return $ret
}}

_keep_env=0
_verbose=1
_sandbox={SANDBOX_SETUP}
_keep_sandbox=0
_args=`getopt -o kqvE -- "$@"`
if [ $? != 0 ] ; then echo "Args parsing failed..." >&2 ; exit 1 ; fi
eval set -- "$_args"

_args=( )
while true ; do
    case "$1" in
        -k) _keep_sandbox=1 ;;
        -q) : $(( _verbose-- )) ;;
        -v) : $(( _verbose++ )) ;;
        -E) _keep_env=1 ;;
        --) shift ; break ;;
        *) echo "Internal error!" ; exit 1 ;;
    esac
    _args+=("$1")
    shift
done

if [[ $# -gt 1 ]] ; then
    echo "Unexpected arguments!" >&2
    exit 1
fi

trap on_exit EXIT

case "${{1:-run}}" in
    run)
        if [[ $_keep_env = 1 ]] ; then
            exec "$0" "${{_args[@]}}" __run
        else
            exec /usr/bin/env -i {WHITELIST} "$0" "${{_args[@]}}" __run
        fi
        ;;
    __run)
        cd "${{0%/*}}/workspace"
        case "$_verbose" in
            0)
                run_script >> ../log.txt 2>&1
                ;;
            1)
                set -o pipefail
                {{
                    {{
                        run_script | tee -a ../log.txt
                    }} 3>&1 1>&2- 2>&3- | tee -a ../log.txt
                }} 1>&2- 2>/dev/null
                ;;
            *)
                set -o pipefail
                {{
                    {{
                        run_script | tee -a ../log.txt
                    }} 3>&1 1>&2- 2>&3- | tee -a ../log.txt
                }} 3>&1 1>&2- 2>&3-
                ;;
        esac
        ;;
    shell)
        if [[ $_keep_env = 1 ]] ; then
            exec /usr/bin/env {ENV} "$0" "${{_args[@]}}" __shell
        else
            exec /usr/bin/env -i {WHITELIST} "$0" "${{_args[@]}}" __shell
        fi
        ;;
    __shell)
        cd "${{0%/*}}/workspace"
        if [[ $_keep_env = 1 ]] ; then
            run /bin/bash -s {ARGS}
        else
            run /bin/bash --norc -s {ARGS}
        fi
        ;;
    *)
        echo "Unknown command" ; exit 1 ;;
esac
"""

    @staticmethod
    def releaseNameFormatter(sandbox, persistent=True):

        def fmt(step, mode):
            if not sandbox or (mode == 'workspace'):
                return os.path.join(
                    BobState().getByNameDirectory(
                        os.path.join("work", step.getPackage().getPath(),
                                     step.getLabel()),
                        asHexStr(step.getDigest()),
                        persistent),
                    "workspace")
            else:
                assert mode == 'exec'
                return os.path.join(asHexStr(step.getDigest()), "workspace")

        return fmt

    @staticmethod
    def developNameFormatter(dirs = {}):

        def fmt(step, mode):
            baseDir = os.path.join("dev", step.getLabel(),
                                   step.getPackage().getPath())
            digest = step.getDigest()
            if digest in dirs:
                res = dirs[digest]
            else:
                num = dirs.setdefault(baseDir, 0) + 1
                res = os.path.join(baseDir, str(num))
                dirs[baseDir] = num
                dirs[digest] = res
            return os.path.join(res, "workspace")

        return fmt

    def __init__(self, recipes, verbose, force, skipDeps, buildOnly, preserveEnv,
                 envWhiteList, globalPaths, sandbox, bobRoot, cleanBuild):
        self.__recipes = recipes
        self.__wasRun = {}
        self.__verbose = max(-2, min(2, verbose))
        self.__force = force
        self.__skipDeps = skipDeps
        self.__buildOnly = buildOnly
        self.__preserveEnv = preserveEnv
        self.__envWhiteList = envWhiteList
        self.__globalPaths = globalPaths
        self.__execBaseDir = os.getcwd()
        self.__currentPackage = None
        self.__archive = DummyArchive()
        self.__doUpload = False
        self.__downloadDepth = 0xffff
        self.__workspaceBaseDir = os.getcwd()
        self.__sandbox = sandbox
        if sandbox:
            self.__execBaseDir = "/bob"
        else:
            self.__execBaseDir = self.__workspaceBaseDir
        self.__bobRoot = bobRoot
        self.__cleanBuild = cleanBuild

    def setArchiveHandler(self, archive):
        self.__archive = archive

    def setDownloadMode(self, mode):
        if mode == 'yes':
            self.__downloadDepth = 0
        elif mode == 'deps':
            self.__downloadDepth = 1
        else:
            assert mode == 'no'
            self.__downloadDepth = 0xffff

    def setUploadMode(self, mode):
        self.__doUpload = mode

    def _wasAlreadyRun(self, unique):
        return unique in self.__wasRun

    def _getAlreadyRun(self, unique):
        return self.__wasRun[unique]

    def _setAlreadyRun(self, unique, data):
        self.__wasRun[unique] = data

    def _constructDir(self, step, label):
        created = False
        workDir = step.getWorkspacePath()
        if not os.path.isdir(workDir):
            os.makedirs(workDir)
            created = True
        return (workDir, created)

    def _runShell(self, step, scriptName):
        workspacePath = step.getWorkspacePath()
        if not os.path.isdir(workspacePath): os.makedirs(workspacePath)

        # construct environment
        stepEnv = step.getEnv().copy()
        stepEnv["PATH"] = ":".join(
            [ os.path.join(self.__execBaseDir, p) for p in step.getPaths() ]
            + self.__globalPaths)
        stepEnv["LD_LIBRARY_PATH"] = ":".join(
            [ os.path.join(self.__execBaseDir, p) for p in step.getLibraryPaths() ])
        stepEnv["BOB_CWD"] = os.path.join(self.__execBaseDir, step.getExecPath())

        # filter runtime environment
        if self.__preserveEnv:
            runEnv = os.environ.copy()
        else:
            runEnv = { k:v for (k,v) in os.environ.items()
                                     if k in self.__envWhiteList }
        runEnv.update(stepEnv)

        # sandbox
        sandbox = []
        sandboxSetup = ""
        if self.__sandbox:
            sandboxSetup = "\"$(mktemp -d)\""
            sandbox.append(quote(os.path.join(self.__bobRoot,
                                              "bin", "namespace-sandbox")))
            sandbox.extend(["-S", "\"$_sandbox\""])
            sandbox.extend(["-W", quote(os.path.join(self.__execBaseDir,
                                                     step.getExecPath()))])
            sandbox.extend(["-H", "bob"])
            sandbox.extend(["-d", "/tmp"])
            for f in os.listdir("work/_sandbox"):
                sandbox.extend([
                    "-M", os.path.join(self.__workspaceBaseDir, "work",
                                       "_sandbox", f),
                    "-m", "/"+f ])
            for (hostPath, sndbxPath) in self.__recipes.buildSandbox()['mount'].items():
                sandbox.extend(["-M", hostPath ])
                if hostPath != sndbxPath:
                    sandbox.extend(["-m", sndbxPath])
            sandbox.extend([
                "-M", quote(os.path.normpath(os.path.join(
                    self.__workspaceBaseDir, step.getWorkspacePath(), ".."))),
                "-w", quote(os.path.normpath(os.path.join(
                    self.__execBaseDir, step.getExecPath(), ".."))) ])
            for s in step.getAllDepSteps():
                if not s.isValid(): continue
                sandbox.extend([
                    "-M", quote(os.path.join(self.__workspaceBaseDir,
                                             s.getWorkspacePath())),
                    "-m", quote(os.path.join(self.__execBaseDir,
                                             s.getExecPath())) ])
            sandbox.append("--")

        # write scripts
        runFile = os.path.join("..", scriptName+".sh")
        absRunFile = os.path.normpath(os.path.join(workspacePath, runFile))
        with open(absRunFile, "w") as f:
            print(LocalBuilder.RUN_TEMPLATE.format(
                    ENV=" ".join(sorted([
                        "{}={}".format(key, quote(value))
                        for (key, value) in stepEnv.items() ])),
                    WHITELIST=" ".join(sorted([
                        '${'+key+'+'+key+'="$'+key+'"}'
                        for key in self.__envWhiteList ])),
                    ARGS=" ".join([
                        quote(os.path.join(self.__execBaseDir, a.getExecPath()))
                        for a in step.getArguments() ]),
                    SANDBOX_CMD=" ".join(sandbox),
                    SANDBOX_SETUP=sandboxSetup
                ), file=f)
        scriptFile = os.path.join(workspacePath, "..", "script")
        with open(scriptFile, "w") as f:
            print("set -o errtrace", file=f)
            print("set -o nounset", file=f)
            print("trap 'RET=$? ; echo \"\x1b[31;1mStep failed on line ${LINENO}: Exit status ${RET}; Command:\x1b[0;31m ${BASH_COMMAND}\x1b[0m\" >&2 ; exit $RET' ERR", file=f)
            print("trap 'for i in \"${_BOB_TMP_CLEANUP[@]-}\" ; do rm -f \"$i\" ; done' EXIT", file=f)
            print("", file=f)
            print("# Special args:", file=f)
            print("declare -A BOB_DEP_PATHS=( {} )".format(" ".join(sorted(
                [ "[{}]={}".format(quote(a.getPackage().getName()), quote(os.path.join(self.__execBaseDir, a.getExecPath())))
                    for a in step.getAllDepSteps() ] ))), file=f)
            print("declare -A BOB_TOOL_PATHS=( {} )".format(" ".join(sorted(
                [ "[{}]={}".format(quote(t), quote(os.path.join(self.__execBaseDir, p)))
                    for (t,p) in step.getTools().items()] ))), file=f)
            print("# Environment:", file=f)
            for (k,v) in sorted(stepEnv.items()):
                print("export {}={}".format(k, quote(v)), file=f)
            print("", file=f)
            print("# BEGIN BUILD SCRIPT", file=f)
            print(step.getScript(), file=f)
            print("# END BUILD SCRIPT", file=f)
        os.chmod(absRunFile, stat.S_IRWXU | stat.S_IRGRP | stat.S_IWGRP |
            stat.S_IROTH | stat.S_IWOTH)
        cmdLine = ["/bin/bash", runFile, "__run"]
        if self.__verbose < 0:
            cmdLine.append('-q')
        elif self.__verbose == 1:
            cmdLine.append('-v')
        elif self.__verbose >= 2:
            cmdLine.append('-vv')

        proc = subprocess.Popen(cmdLine, cwd=step.getWorkspacePath(), env=runEnv)
        try:
            if proc.wait() != 0:
                raise BuildError("Build script {} returned with {}"
                                    .format(absRunFile, proc.returncode))
        except KeyboardInterrupt:
            raise BuildError("User aborted while running {}".format(absRunFile))

    def _info(self, *args, **kwargs):
        if self.__verbose >= -1:
            print(*args, **kwargs)

    def cook(self, steps, parentPackage, done=set(), depth=0):
        currentPackage = self.__currentPackage
        ret = None

        # skip everything except the current package
        if self.__skipDeps:
            steps = [ s for s in steps if s.getPackage() == parentPackage ]

        for step in reversed(steps):
            # skip if already processed steps
            if step in done:
                continue

            # update if package changes
            if step.getPackage() != self.__currentPackage:
                self.__currentPackage = step.getPackage()
                print(">>", colorize("/".join(self.__currentPackage.getStack()), "32;1"))

            # execute step
            ret = None
            try:
                if step.isCheckoutStep():
                    if step.isValid():
                        self._cookCheckoutStep(step, done, depth)
                elif step.isBuildStep():
                    if step.isValid():
                        self._cookBuildStep(step, done, depth)
                else:
                    assert step.isPackageStep() and step.isValid()
                    ret = self._cookPackageStep(step, done, depth)
            except BuildError as e:
                e.pushFrame(step.getPackage().getName())
                raise e

            # mark as done
            done.add(step)

        # back to original package
        if currentPackage != self.__currentPackage:
            self.__currentPackage = currentPackage
            if currentPackage:
                print(">>", colorize("/".join(self.__currentPackage.getStack()), "32;1"))
        return ret

    def _cookCheckoutStep(self, checkoutStep, done, depth):
        checkoutDigest = checkoutStep.getDigest()
        if self._wasAlreadyRun(checkoutDigest):
            prettySrcPath = self._getAlreadyRun(checkoutDigest)
            self._info("   CHECKOUT  skipped (reuse {})".format(prettySrcPath))
        else:
            # depth first
            self.cook(checkoutStep.getAllDepSteps(), checkoutStep.getPackage(),
                      done, depth+1)

            # get directory into shape
            (prettySrcPath, created) = self._constructDir(checkoutStep, "src")
            oldCheckoutState = BobState().getDirectoryState(prettySrcPath, {})
            if created:
                # invalidate result if folder was created
                BobState().delResultHash(prettySrcPath)
                oldCheckoutState = {}
                BobState().setDirectoryState(prettySrcPath, oldCheckoutState)

            checkoutState = checkoutStep.getScmDirectories().copy()
            checkoutState[None] = checkoutDigest
            if self.__buildOnly and (BobState().getResultHash(prettySrcPath) is not None):
                self._info("   CHECKOUT  skipped due to --build-only ({})".format(prettySrcPath))
            elif (self.__force or (not checkoutStep.isDeterministic()) or
                    (BobState().getResultHash(prettySrcPath) is None) or
                    (checkoutState != oldCheckoutState)):
                # move away old or changed source directories
                for (scmDir, scmDigest) in oldCheckoutState.copy().items():
                    if (scmDir is not None) and (scmDigest != checkoutState.get(scmDir)):
                        scmPath = os.path.normpath(os.path.join(prettySrcPath, scmDir))
                        atticName = os.path.basename(scmPath)+"_"+datetime.datetime.now().isoformat()
                        print(colorize("   ATTIC     {} (move to ../attic/{})".format(scmPath, atticName), "33"))
                        atticPath = os.path.join(prettySrcPath, "..", "attic")
                        if not os.path.isdir(atticPath):
                            os.makedirs(atticPath)
                        os.rename(scmPath, os.path.join(atticPath, atticName))
                        del oldCheckoutState[scmDir]
                        BobState().setDirectoryState(prettySrcPath, oldCheckoutState)

                print(colorize("   CHECKOUT  {}".format(prettySrcPath), "32"))
                self._runShell(checkoutStep, "checkout")

                # reflect new checkout state
                BobState().setDirectoryState(prettySrcPath, checkoutState)
            else:
                self._info("   CHECKOUT  skipped (fixed package {})".format(prettySrcPath))

            # We always have to rehash the directory as the user might have
            # changed the source code manually.
            BobState().setResultHash(prettySrcPath, hashDirectory(
                checkoutStep.getWorkspacePath(),
                os.path.join(checkoutStep.getWorkspacePath(), "..", "cache.bin") ))
            self._setAlreadyRun(checkoutDigest, prettySrcPath)

    def _cookBuildStep(self, buildStep, done, depth):
        buildDigest = buildStep.getDigest()
        if self._wasAlreadyRun(buildDigest):
            prettyBuildPath = self._getAlreadyRun(buildDigest)
            self._info("   BUILD     skipped (reuse {})".format(prettyBuildPath))
        else:
            # depth first
            self.cook(buildStep.getAllDepSteps(), buildStep.getPackage(), done, depth+1)

            # get directory into shape
            (prettyBuildPath, created) = self._constructDir(buildStep, "build")
            oldBuildDigest = BobState().getDirectoryState(prettyBuildPath)
            if created or (buildDigest != oldBuildDigest):
                if (oldBuildDigest is not None) and (buildDigest != oldBuildDigest):
                    # build something different -> prune workspace
                    print(colorize("   PRUNE     {} (recipe changed)".format(prettyBuildPath), "33"))
                    emptyDirectory(prettyBuildPath)
                # invalidate build step
                BobState().delInputHashes(prettyBuildPath)
                BobState().delResultHash(prettyBuildPath)

            if buildDigest != oldBuildDigest:
                BobState().setDirectoryState(prettyBuildPath, buildDigest)

            # run build if input has changed
            buildInputHashes = [ BobState().getResultHash(i.getWorkspacePath())
                for i in buildStep.getArguments() if i.isValid() ]
            if (not self.__force) and (BobState().getInputHashes(prettyBuildPath) == buildInputHashes):
                self._info("   BUILD     skipped (unchanged input for {})".format(prettyBuildPath))
            else:
                print(colorize("   BUILD     {}".format(prettyBuildPath), "32"))
                if self.__cleanBuild: emptyDirectory(prettyBuildPath)
                self._runShell(buildStep, "build")
                BobState().setResultHash(prettyBuildPath, datetime.datetime.utcnow())
                BobState().setInputHashes(prettyBuildPath, buildInputHashes)
            self._setAlreadyRun(buildDigest, prettyBuildPath)

    def _cookPackageStep(self, packageStep, done, depth):
        packageDigest = packageStep.getDigest()
        if self._wasAlreadyRun(packageDigest):
            prettyPackagePath = self._getAlreadyRun(packageDigest)
            self._info("   PACKAGE   skipped (reuse {})".format(prettyPackagePath))
        else:
            # get directory into shape
            (prettyPackagePath, created) = self._constructDir(packageStep, "dist")
            oldPackageDigest = BobState().getDirectoryState(prettyPackagePath)
            if created or (packageDigest != oldPackageDigest):
                if (oldPackageDigest is not None) and (packageDigest != oldPackageDigest):
                    # package something different -> prune workspace
                    print(colorize("   PRUNE     {} (recipe changed)".format(prettyPackagePath), "33"))
                    emptyDirectory(prettyPackagePath)
                # invalidate result if folder was created
                BobState().delInputHashes(prettyPackagePath)
                BobState().delResultHash(prettyPackagePath)

            if packageDigest != oldPackageDigest:
                BobState().setDirectoryState(prettyPackagePath, packageDigest)

            # can we just download the result?
            packageDone = False
            packageExecuted = False
            packageBuildId = packageStep.getBuildId()
            if packageBuildId and (depth >= self.__downloadDepth):
                # Fully deterministic package. Should we try to download it or do
                # we already have a result?
                if BobState().getResultHash(prettyPackagePath) is None:
                    if self.__archive.downloadPackage(packageBuildId, prettyPackagePath):
                        BobState().delInputHashes(prettyPackagePath) # no local input involved
                        packageDone = True
                        packageExecuted = True
                else:
                    self._info("   PACKAGE   skipped (deterministic output in {})".format(prettyPackagePath))
                    packageDone = True

            # package it if needed
            if not packageDone:
                # depth first
                self.cook(packageStep.getAllDepSteps(), packageStep.getPackage(), done, depth+1)

                packageInputHashes = [ BobState().getResultHash(i.getWorkspacePath())
                    for i in packageStep.getArguments() if i.isValid() ]
                if (not self.__force) and (BobState().getInputHashes(prettyPackagePath) == packageInputHashes):
                    self._info("   PACKAGE   skipped (unchanged input for {})".format(prettyPackagePath))
                else:
                    print(colorize("   PACKAGE   {}".format(prettyPackagePath), "32"))
                    emptyDirectory(prettyPackagePath)
                    self._runShell(packageStep, "package")
                    packageExecuted = True
                    if packageBuildId and self.__doUpload:
                        self.__archive.uploadPackage(packageBuildId, prettyPackagePath)
            else:
                # do not change input hashes
                packageInputHashes = BobState().getInputHashes(prettyPackagePath)

            # Rehash directory if content was changed
            if packageExecuted:
                BobState().setResultHash(prettyPackagePath, hashDirectory(
                    packageStep.getWorkspacePath(),
                    os.path.join(packageStep.getWorkspacePath(), "..", "cache.bin") ))
                BobState().setInputHashes(prettyPackagePath, packageInputHashes)
            self._setAlreadyRun(packageDigest, prettyPackagePath)

        return prettyPackagePath


def touch(packages):
    for p in packages:
        touch([s.getPackage() for s in p.getAllDepSteps()])
        p.getCheckoutStep().getWorkspacePath()
        p.getBuildStep().getWorkspacePath()
        p.getPackageStep().getWorkspacePath()

def setupSandbox(recipes):
    cfg = recipes.buildSandbox()
    if (cfg['url'] is None) or (cfg['digestSHA1'] == b''):
        print("Sandbox not configured. Building in regular mode...")
        return False
    if cfg['digestSHA1'] == BobState().getSandboxState():
        if os.path.isdir("work/_sandbox"):
            return True
        BobState().setSandboxState() # deleted -> reset state

    try:
        print(">>", colorize("<sandbox>", "32;1"))
        print(colorize("   DOWNLOAD  {}".format(cfg['url']), "32"))
        (localFilename, headers) = urllib.request.urlretrieve(cfg['url'])

        # verify image
        if hashFile(localFilename) != cfg['digestSHA1']:
            raise BuildError("Downloaded sandbox image does not match checksum!")

        # extract sandbox
        print(colorize("   EXTRACT   {}".format(asHexStr(cfg['digestSHA1'])), "32"))
        if os.path.exists("work/_sandbox"):
            removePath("work/_sandbox")
        os.makedirs("work/_sandbox")
        with tarfile.open(localFilename, errorlevel=1) as tf:
            tf.extractall("work/_sandbox")

    except urllib.error.URLError as e:
        raise BuildError("Error downloading sandbox image: " + str(e.reason))
    except OSError as e:
        raise BuildError("Error: " + str(e))
    finally:
        urllib.request.urlcleanup()

    BobState().setSandboxState(cfg['digestSHA1'])

    return True


def commonBuildDevelop(recipes, parser, argv, bobRoot, develop):
    parser.add_argument('packages', metavar='PACKAGE', type=str, nargs='+',
        help="(Sub-)package to build")
    parser.add_argument('--destination', metavar="DEST",
        help="Destination of build result (will be cleaned!)")
    parser.add_argument('-f', '--force', default=False, action='store_true',
        help="Force execution of all build steps")
    parser.add_argument('-n', '--no-deps', default=False, action='store_true',
        help="Don't build dependencies")
    parser.add_argument('-b', '--build-only', default=False, action='store_true',
        help="Don't checkout, just build and package")
    parser.add_argument('-q', '--quiet', default=0, action='count',
        help="Decrease verbosity (may be specified multiple times)")
    parser.add_argument('-v', '--verbose', default=0, action='count',
        help="Increase verbosity (may be specified multiple times)")
    parser.add_argument('-D', default=[], action='append', dest="defines",
        help="Override default environment variable")
    parser.add_argument('-e', dest="white_list", default=[], action='append', metavar="NAME",
        help="Preserve environment variable")
    parser.add_argument('-E', dest="preserve_env", default=False, action='store_true',
        help="Preserve whole environment")
    parser.add_argument('--upload', default=False, action='store_true',
        help="Upload to binary archive")
    parser.add_argument('--download', metavar="MODE", default="no" if develop else "yes",
        help="Download from binary archive (yes, no, deps)", choices=['yes', 'no', 'deps'])
    args = parser.parse_args(argv)

    defines = {}
    for define in args.defines:
        d = define.split("=")
        if len(d) == 1:
            defines[d[0]] = ""
        elif len(d) == 2:
            defines[d[0]] = d[1]
        else:
            parser.error("Malformed define: "+define)

    envWhiteList = recipes.envWhiteList()
    envWhiteList |= set(args.white_list)

    cleanBuild = not develop
    if develop:
        sandboxEnabled = False
    else:
        sandboxEnabled = setupSandbox(recipes)

    if develop:
        nameFormatter = LocalBuilder.developNameFormatter()
        globalPaths = recipes.devGlobalPaths()
    else:
        nameFormatter = LocalBuilder.releaseNameFormatter(sandboxEnabled)
        globalPaths = recipes.buildGlobalPaths()
    rootPackages = recipes.generatePackages(nameFormatter, defines)
    if develop:
        touch(sorted(rootPackages.values(), key=lambda p: p.getName()))

    if (len(args.packages) > 1) and args.destination:
        raise BuildError("Destination may only be specified when building a single package")

    builder = LocalBuilder(recipes, args.verbose - args.quiet, args.force, args.no_deps,
        args.build_only, args.preserve_env, envWhiteList, globalPaths, sandboxEnabled,
        bobRoot, cleanBuild)

    archiveSpec = recipes.archiveSpec()
    archiveBackend = archiveSpec.get("backend", "none")
    if archiveBackend == "file":
        builder.setArchiveHandler(LocalArchive(archiveSpec))
    elif archiveBackend == "http":
        builder.setArchiveHandler(SimpleHttpArchive(archiveSpec))
    elif archiveBackend != "none":
        raise BuildError("Invalid archive backend: "+archiveBackend)
    builder.setUploadMode(args.upload)
    builder.setDownloadMode(args.download)

    for p in args.packages:
        package = walkPackagePath(rootPackages, p)
        prettyResultPath = builder.cook([package.getPackageStep()], package)
        print("Build result is in", prettyResultPath)

    # copy build result if requested
    if args.destination:
        if os.path.exists(args.destination):
            removePath(args.destination)
        shutil.copytree(prettyResultPath, args.destination, symlinks=True)

def doBuild(recipes, argv, bobRoot):
    parser = argparse.ArgumentParser(prog="bob build", description='Build packages in release mode.')
    commonBuildDevelop(recipes, parser, argv, bobRoot, False)

def doDevelop(recipes, argv, bobRoot):
    print(colorize("WARNING: developer mode might exhibit problems and is subject to change! Use with care.", "33"))
    parser = argparse.ArgumentParser(prog="bob dev", description='Build packages in development mode.')
    commonBuildDevelop(recipes, parser, argv, bobRoot, True)

### Clean #############################

def collectPaths(package):
    paths = set()
    checkoutStep = package.getCheckoutStep()
    if checkoutStep.isValid(): paths.add(checkoutStep.getWorkspacePath())
    buildStep = package.getBuildStep()
    if buildStep.isValid(): paths.add(buildStep.getWorkspacePath())
    paths.add(package.getPackageStep().getWorkspacePath())
    for d in package.getDirectDepSteps():
        paths |= collectPaths(d.getPackage())
    return paths

def doClean(recipes, argv, bobRoot):
    parser = argparse.ArgumentParser(prog="bob clean", description='Clean unused directories.')
    parser.add_argument('--dry-run', default=False, action='store_true',
        help="Don't delete, just print what would be deleted")
    parser.add_argument('-v', '--verbose', default=False, action='store_true',
        help="Print what is done")
    args = parser.parse_args(argv)

    # collect all used paths
    rootPackages = recipes.generatePackages(LocalBuilder.releaseNameFormatter(False, False)).values()
    usedPaths = set()
    for root in rootPackages:
        usedPaths |= collectPaths(root)

    # chop off the trailing "/workspace" part
    # FIXME: this looks too brittle
    usedPaths = set([ p[:-10] for p in usedPaths ])

    # get all known existing paths
    allPaths = BobState().getAllNameDirectores()
    allPaths = set([ d for d in allPaths if os.path.exists(d) ])

    # delete unused directories
    for d in allPaths - usedPaths:
        if args.verbose or args.dry_run:
            print("rm", d)
        if not args.dry_run:
            removePath(d)
