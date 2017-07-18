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

from __future__ import absolute_import
import os

import nox


SYSTEM_TEST_ENV_VARS = (
    'GOOGLE_RESUMABLE_MEDIA_BUCKET',
    'GOOGLE_APPLICATION_CREDENTIALS',
)


@nox.session
@nox.parametrize('python_version', ['2.7', '3.4', '3.5', '3.6'])
def unit_tests(session, python_version):
    """Run the unit test suite."""

    # Run unit tests against all supported versions of Python.
    session.interpreter = 'python{}'.format(python_version)

    # Install all test dependencies, then install this package in-place.
    session.install('mock', 'pytest', 'pytest-cov')
    session.install('-e', '.')

    # Run py.test against the unit tests.
    # NOTE: We don't require 100% line coverage for unit test runs since
    #       some have branches that are Py2/Py3 specific.
    line_coverage = '--cov-fail-under=99'
    session.run(
        'py.test',
        '--cov=google.resumable_media', '--cov=tests.unit', '--cov-append',
        '--cov-config=.coveragerc', '--cov-report=', line_coverage,
        os.path.join('tests', 'unit'),
    )


@nox.session
def docs(session):
    """Build the docs."""

    # Build docs against the latest version of Python, because we can.
    session.interpreter = 'python3.6'

    # Install Sphinx and other dependencies.
    session.chdir(os.path.realpath(os.path.dirname(__file__)))
    session.install(
        'sphinx', 'sphinx_rtd_theme', 'sphinx-docstring-typing >= 0.0.3')
    session.install('-e', '.')

    # Build the docs!
    session.run('bash', os.path.join('scripts', 'build_docs.sh'))


@nox.session
def doctest(session):
    """Run the doctests."""
    session.interpreter = 'python3.6'

    # Install Sphinx and other dependencies.
    session.chdir(os.path.realpath(os.path.dirname(__file__)))
    session.install(
        'sphinx',
        'sphinx_rtd_theme',
        'sphinx-docstring-typing >= 0.0.3',
        'mock',
        'google-auth'
    )
    session.install('-e', '.')

    # Run the doctests with Sphinx.
    session.run(
        'sphinx-build', '-W', '-b', 'doctest',
        '-d', os.path.join('docs_build', 'build', 'doctrees'),
        'docs_build', os.path.join('docs_build', 'doctest'),
    )


@nox.session
def lint(session):
    """Run flake8.

    Returns a failure if flake8 finds linting errors or sufficiently
    serious code quality issues.
    """
    session.interpreter = 'python3.6'
    session.install('flake8')
    session.install('-e', '.')
    session.run(
        'flake8',
        os.path.join('google', 'resumable_media'),
        'tests')


@nox.session
def lint_setup_py(session):
    """Verify that setup.py is valid (including RST check)."""
    session.interpreter = 'python3.6'
    session.install('docutils', 'Pygments')
    session.run(
        'python', 'setup.py', 'check', '--restructuredtext', '--strict')


@nox.session
@nox.parametrize('python_version', ['2.7', '3.6'])
def system_tests(session, python_version):
    """Run the system test suite."""

    # Sanity check: environment variables are set.
    missing = []
    for env_var in SYSTEM_TEST_ENV_VARS:
        if env_var not in os.environ:
            missing.append(env_var)

    # Only run system tests if the environment variables are set.
    if missing:
        all_vars = ', '.join(missing)
        msg = 'Environment variable(s) unset: {}'.format(all_vars)
        session.skip(msg)

    # Run the system tests against latest Python 2 and Python 3 only.
    session.interpreter = 'python{}'.format(python_version)

    # Install all test dependencies, then install this package into the
    # virutalenv's dist-packages.
    session.install('mock', 'pytest', 'requests', 'google-auth >= 0.10.0')
    session.install('-e', '.')

    # Run py.test against the system tests.
    session.run('py.test', os.path.join('tests', 'system'))


@nox.session
def cover(session):
    """Run the final coverage report.

    This outputs the coverage report aggregating coverage from the unit
    test runs (not system test runs), and then erases coverage data.
    """
    session.interpreter = 'python3.6'
    session.install('coverage', 'pytest-cov')
    session.run('coverage', 'report', '--show-missing', '--fail-under=100')
    session.run('coverage', 'erase')
