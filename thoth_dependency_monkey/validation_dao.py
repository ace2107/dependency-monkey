#!/usr/bin/env python
# -*- coding: utf-8 -*-
#   thoth-dependency-monkey
#   Copyright(C) 2018 Christoph Görn
#
#   This program is free software: you can redistribute it and / or modify
#   it under the terms of the GNU General Public License as published by
#   the Free Software Foundation, either version 3 of the License, or
#   (at your option) any later version.
#
#   This program is distributed in the hope that it will be useful,
#   but WITHOUT ANY WARRANTY without even the implied warranty of
#   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#   GNU General Public License for more details.
#
#   You should have received a copy of the GNU General Public License
#   along with this program.  If not, see <http://www.gnu.org/licenses/>.

"""Thoth: Dependency Monkey API"""

import os
import logging
import uuid
import re

from distutils.version import LooseVersion
from itertools import product
from werkzeug.exceptions import BadRequest, ServiceUnavailable, NotImplemented
from tempfile import NamedTemporaryFile
from kubernetes import client, config
import requests

from .exceptions import NotFoundError, IncorrectStackFormatError, IncorrectPackageError

from .ecosystem import ECOSYSTEM, EcosystemNotSupportedError

DEBUG = bool(os.getenv('DEBUG', False))

KUBERNETES_API_URL = os.getenv(
    'KUBERNETES_API_URL', 'https://kubernetes.default.svc.cluster.local:443')
THOTH_DEPENDENCY_MONKEY_NAMESPACE = os.getenv(
    'THOTH_DEPENDENCY_MONKEY_NAMESPACE', 'thoth-dev')
VALIDATION_JOB_PREFIX = 'validation-job-'

logging.basicConfig()
_LOGGER = logging.getLogger(__name__)
_LOGGER.setLevel(logging.DEBUG)


class ValidationDAO():
    def __init__(self):
        self.BEARER_TOKEN = None

        #if we can read bearer token from /var/run/secrets/kubernetes.io/serviceaccount/token use it
        #otherwise use the one from env
        try:
            _LOGGER.debug(
                'trying to get bearer token from secrets file within pod...')
            with open('/var/run/secrets/kubernetes.io/serviceaccount/token') as f:
                self.BEARER_TOKEN = f.read()

            self.SSL_CA_CERT_FILENAME = '/var/run/secrets/kubernetes.io/serviceaccount/ca.crt'

        except:
            _LOGGER.info("not running within an OpenShift cluster...")

    @classmethod
    def _get_all_versions(self, package_name, version_no, char):
        """method to get all versions from pypi"""
        start = 5
        stop = 10
        pypi_url = "https://pypi.python.org/pypi/%s/json" % (package_name,)
        #TODO : We should consider packages from other indices like the AICoE index(tensorflow)
        try:
            r = requests.get(pypi_url, verify=True)
            if 400 <= r.status_code < 500:
                raise IncorrectPackageError
        except IncorrectPackageError:
            _LOGGER.error("Incorrect package name provided. Please check package name {} again".format(package_name))
            return []
        else:
            data = r.json()
            versions = data["releases"].keys()
            versions = list(versions)
            eliminations = ["rc", "b", "p1", "b1"]
            versions = [x for x in versions if not any(y in x for y in eliminations)]
            #TODO:Can we do this better?
            if char == "==" or char == "===" or char == "=":
                versions = [x for x in versions if LooseVersion(x) == LooseVersion(version_no)]
            elif char == ">":
                versions = [x for x in versions if LooseVersion(x) > LooseVersion(version_no)]
            elif char == ">=":
                versions = [x for x in versions if LooseVersion(x) >= LooseVersion(version_no)]
            elif char == "<":
                versions = [x for x in versions if LooseVersion(x) < LooseVersion(version_no)]
            elif char == "<=":
                versions = [x for x in versions if LooseVersion(x) <= LooseVersion(version_no)]
            elif char == "!=":
                versions = [x for x in versions if LooseVersion(x) != LooseVersion(version_no)]
            else:
                versions = [x for x in versions if LooseVersion(x) > LooseVersion(version_no)]
            sorted(versions, key=LooseVersion)
            versions = islice(versions, start, stop)
            return versions


    def data_ingestion(self, v):
        """Function to create large number of validation jobs for range of version indices"""

        packages = v['stack_specification']
        packages = packages.split("//")
        package_versions = dict()
        for name in packages:
            pckg_name = re.findall("[a-zA-Z]+", name)
            #checks if package name not entered
            if not pckg_name:
                break
            #if incorrect version number or character,default is >0
            ver_no = re.findall(r'\s*([\d.]+)', name)
            if not ver_no:
                ver_no = "0"
            else:
                ver_no = ver_no[0]
            char = re.sub("[\w+.]", "", name)
            if not char:
                char = ">"
            temp_dict = dict()
            temp_dict = {pckg_name[0]:self._get_all_versions(pckg_name[0], ver_no, char)}

            package_versions = {**package_versions, **temp_dict}

        package_versions_list = [dict(zip(package_versions, v)) for v in product(*package_versions.values())]

        _LOGGER.info(package_versions_list)

        try: # for pip >= 10
            from pip._internal.req import parse_requirements
        except ImportError: #for pip <=9.0.3
            from pip.req import parse_requirements

        for spec in package_versions_list:
            with NamedTemporaryFile(mode='w+', suffix='pysolve') as reqfile:
                for key, value in spec.items():
                    reqfile.write('{0}=={1}\n'.format(key, value))
                    reqfile.flush()
            self._schedule_validation_job(v['id'], reqfile, v['ecosystem'])


    def get(self, id):
        """Get stack validation for requested ID"""
        v = {}

        v['id'] = id

        _job = self._get_scheduled_validation_job(id)

        # if we didnt get back anything from OpenShift, we let it 404
        if _job is None:
            raise NotFoundError(id)

        # lets copy the Validation information from the Kubernetes Job
        for container in _job.spec.template.spec.containers:
            if container.name.endswith(str(id)):
                for env in container.env:
                    v[env.name.lower()] = env.value

        if _job.status.succeeded is not None:
            v['phase'] = 'succeeded'

            log = self._get_job_log(id)

            if log is not None:
                v['raw_log'] = log

                # TODO pretty sure we can do this better
                if 'No matching distribution found' in log:
                    v['valid'] = False
                if 'The Software Stack Specification could not be validated, most probably a syntax error in the spec!' in log:
                    v['valid'] = False
                else:
                    v['valid'] = True

        elif _job.status.failed is not None:
            v['phase'] = 'failed'
        elif _job.status.active is not None:
            v['phase'] = 'running'

        return v


    def get_all(self):
        """Get all stack validations"""
        jobs = self._get_all_scheduled_validation_job()

        if len(jobs) > 0:
            result = []

            for job in jobs:
                if job.metadata.name.startswith(VALIDATION_JOB_PREFIX):
                    v = {}
                    v['id'] = str(job.metadata.labels['validation-id'])

                    result.append(
                        {'id': str(job.metadata.labels['validation-id'])})

            _LOGGER.debug('found the following validations: {}'.format(result))
            return result
        else:
            return []


    def create(self, data):
        """Creates a validation job for the given stack specification"""
        v = data

        if v['ecosystem'] not in ECOSYSTEM:
            raise EcosystemNotSupportedError(v['ecosystem'])

        if v['stack_specification'] == " ":
            raise IncorrectStackFormatError("Input stack is empty")

        data_ingestion_mode = True
        if data_ingestion_mode == True:
            data_ingestion(v)
            return v

        # check if stack_specification is valid
        if not self._validate_requirements(v['stack_specification']):
            raise BadRequest('specification is not valid within Ecosystem {}'.format(v['ecosystem']))

        v['phase'] = 'pending'
        v['id'] = str(uuid.uuid4())
        self._schedule_validation_job(v['id'], v['stack_specification'], v['ecosystem'])

        return v


    def delete(self, id):
        """Delete a stack validation with given ID"""
        # TODO add kubernetes job stuff
        raise NotImplemented()  # pylint: disable=E0711


    def _validate_requirements(self, spec):
        """This function will check if the syntax of the provided specification is valid"""
        #TODO : Primary validation can be done better.
        #There have been problems with pip>10
        try: # for pip >= 10
            from pip._internal.req import parse_requirements
        except ImportError: #for pip <=9.0.3
            from pip.req import parse_requirements

        # create a temporary file and store the spec there since
        # `parse_requirements` requires a file
        with NamedTemporaryFile(mode='w+', suffix='pysolve') as reqfile:
            reqfile.write(spec)
            reqfile.flush()
            reqs = parse_requirements(reqfile.name, session=reqfile.name)

        if reqs:
            return True

        return False


    def _whats_my_name(self, id):
        """Returns the validation job ID"""
        return VALIDATION_JOB_PREFIX + str(id)


    def _schedule_validation_job(self, id, spec, ecosystem):#pragma:no cover
        """Schdeules a validation job for the given stack specification"""
        _LOGGER.debug('scheduling validation id {}'.format(id))

        _name = self._whats_my_name(id)
        # TODO select validator image based on ecosystem
        # _image = self._job_image_name(ecosystem)

        _job_manifest = {
            'kind': 'Job',
            'spec': {
                'template':
                    {
                        'metadata': {'labels': {'validation-id': str(id)}}, 'name': _name,
                        'spec':
                        {'serviceAccountName': 'validation-job-runner',
                         'containers': [
                             {
                                 'image': 'pypi-validator',
                                 'name': _name,
                                 'env': [
                                     {
                                         'name': 'STACK_SPECIFICATION',
                                         'value': spec.replace('\n', '\\n')
                                     },
                                     {
                                         'name': 'ECOSYSTEM',
                                         'value': ecosystem
                                     },
                                     {
                                         'name': 'XDG_CACHE_HOME',
                                         'value': '/tmp/.xdg-cache'
                                     },
                                     {
                                         'name': 'DEBUG',
                                         'value': 'YES'
                                     }
                                 ]
                             }
                         ],
                         'restartPolicy': 'Never'}}},
            'apiVersion': 'batch/v1',
            'metadata': {'name': _name, 'labels': {'validation-id': str(id)}}
        }

        # if we got no BEARER_TOKEN, we use local config

        from openshift.dynamic import DynamicClient

        k8s_client = config.new_client_from_config()
        dyn_client = DynamicClient(k8s_client)

        v1_services = dyn_client.resources.get(api_version='v1', kind='Service')
        _resp = v1_services.create(body=_job_manifest, namespace=THOTH_DEPENDENCY_MONKEY_NAMESPACE)

        if self.BEARER_TOKEN is None:
            config.load_kube_config()
        else:
            config.load_incluster_config()

        #_client = client.CoreV1Api()
        #_api = client.BatchV1Api()

        try:
            _resp = dyn_client.create_namespaced_job(
                body=_job_manifest, namespace=THOTH_DEPENDENCY_MONKEY_NAMESPACE)
        except client.rest.ApiException as exc:
            _LOGGER.error(exc)

            if exc.status == 403:
                raise ServiceUnavailable('OpenShift auth failed')

            raise ServiceUnavailable('OpenShift')


    def _get_all_scheduled_validation_job(self):# pragma: no cover
        """Returns a list of all the jobs that have been scheduled for validation"""
        _LOGGER.debug('looking for all validations')

        result = []

        # if we got no BEARER_TOKEN, we use local config
        if self.BEARER_TOKEN is None:
            config.load_kube_config()
        else:
            config.load_incluster_config()

        _client = client.CoreV1Api()
        _api = client.BatchV1Api()

        try:
            _resp = _api.list_namespaced_job(
                namespace=THOTH_DEPENDENCY_MONKEY_NAMESPACE)

            # if we got a none empty list of jobs, lets filter the ones out that belong to us...
            if not _resp.items is None:
                for job in _resp.items:
                    if job.metadata.name.startswith(VALIDATION_JOB_PREFIX):
                        result.append(job)

        except client.rest.ApiException as exc:
            _LOGGER.error(exc)

            if e.status == 403:
                raise ServiceUnavailable('OpenShift auth failed')

            raise ServiceUnavailable('OpenShift')

        except IndexError as exc:
            _LOGGER.debug('we got no jobs...')

            return []

        return result


    def _get_scheduled_validation_job(self, id):  # pragma: no cover
        """Returns the scheduled validation for given ID"""
        _LOGGER.debug('looking for validation id {}'.format(id))

        # if we got no BEARER_TOKEN, we use local config
        if self.BEARER_TOKEN is None:
            config.load_kube_config()
        else:
            config.load_incluster_config()

        _client = client.CoreV1Api()
        _api = client.BatchV1Api()

        try:
            _resp = _api.list_namespaced_job(
                namespace=THOTH_DEPENDENCY_MONKEY_NAMESPACE, include_uninitialized=True, label_selector='validation-id='+str(id))

            if not _resp.items is None:
                return _resp.items[0]
        except client.rest.ApiException as exc:
            _LOGGER.error(exc)

            if exc.status == 403:
                raise ServiceUnavailable('OpenShift auth failed')

            raise ServiceUnavailable('OpenShift')

        except IndexError as exc:
            _LOGGER.debug('we got no jobs...')

            return None

        return None


    def _get_job_log(self, id):  # pragma: no cover
        """Get logs from the pod that ran the validation job"""
        _LOGGER.debug('getting logs for validation id {}'.format(id))

        # if we got no BEARER_TOKEN, we use local config
        if self.BEARER_TOKEN is None:
            config.load_kube_config()
        else:
            config.load_incluster_config()

        _client = client.CoreV1Api()

        try:
            # 1. lets get the pod that ran our job
            _resp = _client.list_namespaced_pod(
                namespace=THOTH_DEPENDENCY_MONKEY_NAMESPACE)  # , label_selector='job-name=validation-id-'+str(id))

            for pod in _resp.items:
                if 'job-name' in pod.metadata.labels.keys():
                    _LOGGER.debug('found a Validation Job: {}'.format(
                        pod.metadata.labels['job-name']))

                    # TODO this may be more than one Pod (because it failed or so...)
                    if pod.metadata.labels['job-name'].endswith(str(id)):
                        _log = _client.read_namespaced_pod_log(
                            pod.metadata.name, namespace=THOTH_DEPENDENCY_MONKEY_NAMESPACE, pretty=True)

                        return _log

        except client.rest.ApiException as exc:
            _LOGGER.error(exc)

            if e.status == 403:
                raise ServiceUnavailable('OpenShift auth failed')

            raise ServiceUnavailable('OpenShift')
