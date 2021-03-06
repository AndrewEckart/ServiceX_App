# Copyright (c) 2019, IRIS-HEP
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# * Redistributions of source code must retain the above copyright notice, this
#   list of conditions and the following disclaimer.
#
# * Redistributions in binary form must reproduce the above copyright notice,
#   this list of conditions and the following disclaimer in the documentation
#   and/or other materials provided with the distribution.
#
# * Neither the name of the copyright holder nor the names of its
#   contributors may be used to endorse or promote products derived from
#   this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
# OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
import base64
import zipfile

import pytest
import re
from servicex import TransformerManager

from tests.resource_test_base import ResourceTestBase


def _arg_value(args, param):
    return re.search(param + ' (\\S+)', args[0]).group(1)


def _env_value(env_list, env_name):
    return [x for x in env_list if x.name == env_name][0].value


class TestTransformerManager(ResourceTestBase):

    def test_init_external_kubernetes(self, mocker):
        import kubernetes
        mock_kubernetes = mocker.patch.object(kubernetes.config, 'load_kube_config')
        TransformerManager('external-kubernetes')
        mock_kubernetes.assert_called()

    def test_init_internal_kubernetes(self, mocker):
        import kubernetes
        mock_kubernetes = mocker.patch.object(kubernetes.config, 'load_incluster_config')
        TransformerManager('internal-kubernetes')
        mock_kubernetes.assert_called()

    def test_init_invalid_config(self, mocker):
        import kubernetes
        mock_kubernetes_inside = mocker.patch.object(kubernetes.config, 'load_incluster_config')
        mock_kubernetes_outside = mocker.patch.object(kubernetes.config, 'load_kube_config')

        with pytest.raises(ValueError):
            TransformerManager('foo')
            mock_kubernetes_inside.assert_not_called()
            mock_kubernetes_outside.assert_not_called()

    def test_launch_transformer_jobs(self, mocker, mock_rabbit_adaptor):
        import kubernetes

        mocker.patch.object(kubernetes.config, 'load_kube_config')
        mock_kubernetes = mocker.patch.object(kubernetes.client, 'BatchV1Api')

        transformer = TransformerManager('external-kubernetes')
        client = self._test_client(transformation_manager=transformer,
                                   rabbit_adaptor=mock_rabbit_adaptor)

        with client.application.app_context():
            transformer.launch_transformer_jobs(
                image='sslhep/servicex-transformer:pytest', request_id='1234', workers=17,
                chunk_size=5000, rabbitmq_uri='ampq://test.com', namespace='my-ns',
                result_destination='kafka', result_format='arrow', x509_secret='x509',
                generated_code_cm=None)
            called_job = mock_kubernetes.mock_calls[1][2]['body']
            assert called_job.spec.parallelism == 17
            assert len(called_job.spec.template.spec.containers) == 1
            container = called_job.spec.template.spec.containers[0]
            assert container.image == 'sslhep/servicex-transformer:pytest'
            assert container.image_pull_policy == 'Always'
            args = container.args

            assert _arg_value(args, '--rabbit-uri') == 'ampq://test.com'
            assert _arg_value(args, '--chunks') == '5000'
            assert _arg_value(args, '--result-destination') == 'kafka'

            assert mock_kubernetes.mock_calls[1][2]['namespace'] == 'my-ns'

    def test_launch_transformer_with_hostpath(self, mocker, mock_rabbit_adaptor):
        import kubernetes

        mocker.patch.object(kubernetes.config, 'load_kube_config')
        mock_kubernetes = mocker.patch.object(kubernetes.client, 'BatchV1Api')
        additional_config = {
            'TRANSFORMER_LOCAL_PATH': '/tmp/foo'
        }

        transformer = TransformerManager('external-kubernetes')
        client = self._test_client(additional_config=additional_config,
                                   transformation_manager=transformer,
                                   rabbit_adaptor=mock_rabbit_adaptor)

        with client.application.app_context():
            transformer.launch_transformer_jobs(
                image='sslhep/servicex-transformer:pytest', request_id='1234', workers=17,
                chunk_size=5000, rabbitmq_uri='ampq://test.com', namespace='my-ns',
                result_destination='kafka', result_format='arrow', x509_secret='x509',
                generated_code_cm=None)

            called_job = mock_kubernetes.mock_calls[1][2]['body']
            container = called_job.spec.template.spec.containers[0]
            assert container.volume_mounts[0].mount_path == '/etc/grid-security-ro'
            assert called_job.spec.template.spec.volumes[0].secret.secret_name == 'x509'

            assert container.volume_mounts[1].mount_path == '/data'
            assert called_job.spec.template.spec.volumes[1].host_path.path == '/tmp/foo'

    def test_launch_transformer_jobs_with_generated_code(self, mocker, mock_rabbit_adaptor):
        import kubernetes

        mocker.patch.object(kubernetes.config, 'load_kube_config')
        mock_kubernetes = mocker.patch.object(kubernetes.client, 'BatchV1Api')

        transformer = TransformerManager('external-kubernetes')
        client = self._test_client(transformation_manager=transformer,
                                   rabbit_adaptor=mock_rabbit_adaptor)

        with client.application.app_context():
            transformer.launch_transformer_jobs(
                image='sslhep/servicex-transformer:pytest', request_id='1234', workers=17,
                chunk_size=5000, rabbitmq_uri='ampq://test.com', namespace='my-ns',
                result_destination='kafka',
                result_format='parquet', x509_secret='x509',
                generated_code_cm="my-config-map")
            called_job = mock_kubernetes.mock_calls[1][2]['body']
            container = called_job.spec.template.spec.containers[0]
            config_map_vol_mount = container.volume_mounts[1]
            assert config_map_vol_mount.name == 'generated-code'
            assert config_map_vol_mount.mount_path == '/generated'

            config_map_vol = called_job.spec.template.spec.volumes[1]
            assert config_map_vol.name == 'generated-code'
            assert config_map_vol.config_map.name == 'my-config-map'

    def test_launch_transformer_jobs_with_object_store(self, mocker, mock_rabbit_adaptor):
        import kubernetes

        mocker.patch.object(kubernetes.config, 'load_kube_config')
        mock_kubernetes = mocker.patch.object(kubernetes.client, 'BatchV1Api')

        transformer = TransformerManager('external-kubernetes')
        my_config = {
            'OBJECT_STORE_ENABLED': True,
            'MINIO_URL_TRANSFORMER': 'rolling-snail-minio:9000',
            'MINIO_ACCESS_KEY': 'itsame',
            'MINIO_SECRET_KEY': 'shhh'
        }

        client = self._test_client(additional_config=my_config,
                                   transformation_manager=transformer,
                                   rabbit_adaptor=mock_rabbit_adaptor)

        with client.application.app_context():
            transformer.launch_transformer_jobs(
                image='sslhep/servicex-transformer:pytest', request_id='1234', workers=17,
                chunk_size=5000, rabbitmq_uri='ampq://test.com', namespace='my-ns',
                result_destination='object-store',
                result_format='parquet', x509_secret='x509',
                generated_code_cm=None)
            called_job = mock_kubernetes.mock_calls[1][2]['body']
            container = called_job.spec.template.spec.containers[0]
            args = container.args
            assert _arg_value(args, '--result-destination') == 'object-store'
            assert _arg_value(args, '--result-format') == 'parquet'

            env = container.env
            assert _env_value(env, 'MINIO_URL') == 'rolling-snail-minio:9000'
            assert _env_value(env, 'MINIO_ACCESS_KEY') == 'itsame'
            assert _env_value(env, 'MINIO_SECRET_KEY') == 'shhh'

    def test_launch_transformer_jobs_with_kafka_broker(self, mocker, mock_rabbit_adaptor):
        import kubernetes

        mocker.patch.object(kubernetes.config, 'load_kube_config')
        mock_kubernetes = mocker.patch.object(kubernetes.client, 'BatchV1Api')

        transformer = TransformerManager('external-kubernetes')

        client = self._test_client(transformation_manager=transformer,
                                   rabbit_adaptor=mock_rabbit_adaptor)

        with client.application.app_context():
            transformer.launch_transformer_jobs(
                image='sslhep/servicex-transformer:pytest', request_id='1234', workers=17,
                chunk_size=5000, rabbitmq_uri='ampq://test.com', namespace='my-ns',
                result_destination='kafka', result_format='arrow',
                kafka_broker='kafka.servicex.org', x509_secret='x509',
                generated_code_cm=None)
            called_job = mock_kubernetes.mock_calls[1][2]['body']
            container = called_job.spec.template.spec.containers[0]
            args = container.args
            assert _arg_value(args, '--result-destination') == 'kafka'
            assert _arg_value(args, '--brokerlist') == 'kafka.servicex.org'

    def test_shutdown_transformer_jobs(self, mocker, mock_rabbit_adaptor):
        import kubernetes
        mocker.patch.object(kubernetes.config, 'load_kube_config')
        mock_api = mocker.MagicMock(kubernetes.client.BatchV1Api)
        mocker.patch.object(kubernetes.client, 'BatchV1Api',
                            return_value=mock_api)

        mock_delete_options = mocker.MagicMock()
        md = mocker.patch.object(kubernetes.client, 'V1DeleteOptions',
                                 return_value=mock_delete_options)

        transformer = TransformerManager('external-kubernetes')
        transformer.shutdown_transformer_job('1234', 'my-ns')
        md.assert_called_with(propagation_policy='Background')
        mock_api.delete_namespaced_job.assert_called_with(name='transformer-1234',
                                                          body=mock_delete_options,
                                                          namespace='my-ns')

    def test_create_configmap_from_zip(self, mocker):
        import kubernetes
        mocker.patch.object(kubernetes.config, 'load_kube_config')
        mock_api = mocker.MagicMock(kubernetes.client.CoreV1Api)
        mocker.patch.object(kubernetes.client, 'CoreV1Api',
                            return_value=mock_api)

        mock_create_namespaced_config_map = mocker.Mock()
        mock_api.create_namespaced_config_map = mock_create_namespaced_config_map

        transformer = TransformerManager('external-kubernetes')
        mock_zip = mocker.MagicMock(zipfile.ZipFile)
        mock_zip_ext = mocker.Mock()
        mock_zip_ext.filename = 'foo.sh'
        mock_zip.filelist = [mock_zip_ext]
        mock_open = mocker.Mock()
        mock_open.read = mocker.Mock(return_value=b'hi there')
        mock_zip.open = mocker.Mock(return_value=mock_open)

        transformer.create_configmap_from_zip(mock_zip, "my-request", "servicex")

        mock_create_namespaced_config_map.assert_called()
        calls = mock_create_namespaced_config_map.call_args
        "foo.sh" in calls[1]['body'].binary_data.keys()
        assert calls[1]['body'].binary_data['foo.sh'] == \
            base64.b64encode(b"hi there").decode("ascii")
        assert calls[1]['namespace'] == 'servicex'
        assert calls[1]['body'].metadata.name == 'my-request-generated-source'
