#!/bin/bash
#
# Copyright 2017 Google Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# Build the google-resumable-media docs.

set -e

rm -rf docs_build/build/* docs/latest/* docs_build/*rst
OPTIONS="members,inherited-members,undoc-members,show-inheritance"
SPHINX_APIDOC_OPTIONS="${OPTIONS}" sphinx-apidoc \
  --separate --force \
  --output-dir docs_build/ \
  google
# We only have one package, so modules.rst is overkill.
rm -f docs_build/modules.rst
rm -f docs_build/google.rst
mv docs_build/google.resumable_media.rst docs_build/index.rst
python scripts/rewrite_index_rst.py
python scripts/rewrite_requests_pkg_rst.py

# If anything has changed, raise an error (to make sure it gets checked in).
if [[ -n "$(git diff -- docs_build/)" ]]; then
    echo "sphinx-apidoc generated changes that are not checked in to version control."
    exit 1
fi

sphinx-build -W \
  -b html \
  -d docs_build/build/doctrees \
  docs_build/ \
  docs/latest/
echo "Build finished. The HTML pages are in docs/latest."

# If this is a CI build, we want to make sure the docs are already
# checked in as is.
if [ -n "${CIRCLECI}" ] || ["$1" -eq "kokoro" ]
then
    echo "On a CI build, making sure docs already checked in."
    # Pre-emptively ignore changes to the buildinfo file.
    git checkout docs/latest/.buildinfo
    # If anything has changed, raise an error (to fail the build).
    if [[ -n "$(git diff -- docs/)" ]]; then
        echo "Some docs changes are not checked in to version control."
        git status
        exit 1
    fi
fi
