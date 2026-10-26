from django.test import TestCase, override_settings
from django.urls import Resolver404, resolve, reverse


class HealthEndpointTests(TestCase):
    def test_health_is_public_and_reports_db(self):
        resp = self.client.get(reverse('health'))
        self.assertEqual(resp.status_code, 200)
        status = resp.json()['status']
        self.assertTrue(status['ok'])
        self.assertTrue(status['db'])
        self.assertIn('redis', status)

    @override_settings(REDIS_URL='redis://127.0.0.1:1/0', DEBUG=False)
    def test_health_reports_unreachable_redis_quickly_without_leaking_errors(self):
        resp = self.client.get(reverse('health'))
        self.assertEqual(resp.status_code, 503)
        status = resp.json()['status']
        self.assertFalse(status['redis'])
        self.assertNotIn('redis_error', status)


class ApiRootTests(TestCase):
    def test_api_root_is_public_and_links_resolve(self):
        resp = self.client.get(reverse('portal-root'))
        self.assertEqual(resp.status_code, 200)
        endpoints = resp.json()['endpoints']
        # Every advertised HTTP endpoint must exist (login path used to 404).
        for name, path in endpoints.items():
            if path.startswith('/ws/'):
                continue
            try:
                resolve(path.replace('<slug>', 'any-church'))
            except Resolver404:
                self.fail(f'{name} -> {path} is not routed')
        self.assertEqual(self.client.get('/api/v1/').status_code, 200)
