from contextlib import contextmanager
from testtools import TestCase
from mock import (
    patch,
    MagicMock,
    call,
)
from urlparse import urlparse
from charmhelpers import fetch
import yaml

FAKE_APT_CACHE = {
    # an installed package
    'vim': {
        'current_ver': '2:7.3.547-6ubuntu5'
    },
    # a uninstalled installation candidate
    'emacs': {
    }
}


def fake_apt_cache():
    def _get(package):
        pkg = MagicMock()
        if package not in FAKE_APT_CACHE:
            raise KeyError
        pkg.name = package
        if 'current_ver' in FAKE_APT_CACHE[package]:
            pkg.current_ver = FAKE_APT_CACHE[package]['current_ver']
        else:
            pkg.current_ver = None
        return pkg
    cache = MagicMock()
    cache.__getitem__.side_effect = _get
    return cache


@contextmanager
def patch_open():
    '''Patch open() to allow mocking both open() itself and the file that is
    yielded.

    Yields the mock for "open" and "file", respectively.'''
    mock_open = MagicMock(spec=open)
    mock_file = MagicMock(spec=file)

    @contextmanager
    def stub_open(*args, **kwargs):
        mock_open(*args, **kwargs)
        yield mock_file

    with patch('__builtin__.open', stub_open):
        yield mock_open, mock_file


class FetchTest(TestCase):
    @patch('apt_pkg.Cache')
    def test_filter_packages_missing(self, cache):
        cache.side_effect = fake_apt_cache
        result = fetch.filter_installed_packages(['vim', 'emacs'])
        self.assertEquals(result, ['emacs'])

    @patch('apt_pkg.Cache')
    def test_filter_packages_none_missing(self, cache):
        cache.side_effect = fake_apt_cache
        result = fetch.filter_installed_packages(['vim'])
        self.assertEquals(result, [])

    @patch.object(fetch, 'log')
    @patch('apt_pkg.Cache')
    def test_filter_packages_not_available(self, cache, log):
        cache.side_effect = fake_apt_cache
        result = fetch.filter_installed_packages(['vim', 'joe'])
        self.assertEquals(result, ['joe'])
        log.assert_called_with('Package joe has no installation candidate.',
                               level='WARNING')

    @patch('subprocess.check_call')
    def test_add_source_ppa(self, check_call):
        source = "ppa:test-ppa"
        fetch.add_source(source=source)
        check_call.assert_called_with(['add-apt-repository',
                                       '--yes',
                                       source])

    @patch('subprocess.check_call')
    def test_add_source_http(self, check_call):
        source = "http://archive.ubuntu.com/ubuntu raring-backports main"
        fetch.add_source(source=source)
        check_call.assert_called_with(['add-apt-repository',
                                       '--yes',
                                       source])

    @patch.object(fetch, 'filter_installed_packages')
    @patch.object(fetch, 'apt_install')
    def test_add_source_cloud(self, apt_install, filter_pkg):
        source = "cloud:havana-updates"
        result = '''# Ubuntu Cloud Archive
deb http://ubuntu-cloud.archive.canonical.com/ubuntu havana-updates main
'''
        with patch_open() as (mock_open, mock_file):
            fetch.add_source(source=source)
            mock_file.write.assert_called_with(result)
        filter_pkg.assert_called_with(['ubuntu-cloud-keyring'])

    @patch.object(fetch, 'lsb_release')
    def test_add_source_proposed(self, lsb_release):
        source = "proposed"
        result = """# Proposed
deb http://archive.ubuntu.com/ubuntu precise-proposed main universe multiverse restricted
"""
        lsb_release.return_value = {'DISTRIB_CODENAME': 'precise'}
        with patch_open() as (mock_open, mock_file):
            fetch.add_source(source=source)
            mock_file.write.assert_called_with(result)

    @patch('subprocess.check_call')
    def test_add_source_http_and_key(self, check_call):
        source = "http://archive.ubuntu.com/ubuntu raring-backports main"
        key = "akey"
        fetch.add_source(source=source, key=key)
        check_call.assert_has_calls([
            call(['add-apt-repository', '--yes', source]),
            call(['apt-key', 'import', key])
        ])

    @patch.object(fetch, 'config')
    @patch.object(fetch, 'add_source')
    def test_configure_sources_single_source(self, add_source, config):
        config.side_effect = ['source', 'key']
        fetch.configure_sources()
        add_source.assert_called_with('source', 'key')

    @patch.object(fetch, 'config')
    @patch.object(fetch, 'add_source')
    def test_configure_sources_single_source_no_key(self, add_source, config):
        config.side_effect = ['source', None]
        fetch.configure_sources()
        add_source.assert_called_with('source', None)

    @patch.object(fetch, 'config')
    @patch.object(fetch, 'add_source')
    def test_configure_sources_multiple_sources(self, add_source, config):
        sources = ["sourcea", "sourceb"]
        keys = ["keya", None]
        config.side_effect = [
            yaml.dump(sources),
            yaml.dump(keys)
        ]
        fetch.configure_sources()
        add_source.assert_has_calls([
            call('sourcea', 'keya'),
            call('sourceb', None)
        ])

    @patch.object(fetch, 'config')
    @patch.object(fetch, 'add_source')
    def test_configure_sources_missing_keys(self, add_source, config):
        sources = ["sourcea", "sourceb"]
        keys = ["keya"]  # Second key is missing
        config.side_effect = [
            yaml.dump(sources),
            yaml.dump(keys)
        ]
        self.assertRaises(fetch.SourceConfigError, fetch.configure_sources)

    @patch.object(fetch, 'apt_update')
    @patch.object(fetch, 'config')
    @patch.object(fetch, 'add_source')
    def test_configure_sources_apt_update_called(self, add_source, config,
                                                 apt_update):
        config.side_effect = ['source', 'key']
        fetch.configure_sources(update=True)
        add_source.assert_called_with('source', 'key')
        apt_update.assertCalled()


class InstallTest(TestCase):

    def setUp(self):
        super(InstallTest, self).setUp()
        self.valid_urls = (
            "http://example.com/foo.tar.gz",
            "http://example.com/foo.tgz",
            "http://example.com/foo.tar.bz2",
            "http://example.com/foo.tbz2",
            "http://example.com/foo.zip",
            "http://example.com/foo.zip?bar=baz&x=y#whee",
            "ftp://example.com/foo.tar.gz",
            "https://example.com/foo.tgz",
            "file://example.com/foo.tar.bz2",
            "bzr+ssh://example.com/branch-name",
            "bzr+ssh://example.com/branch-name/",
            "lp:branch-name",
            "lp:example/branch-name",
        )
        self.invalid_urls = (
            "git://example.com/foo.tar.gz",
            "http://example.com/foo",
            "http://example.com/foobar=baz&x=y#tar.gz",
            "http://example.com/foobar?h=baz.zip",
            "abc:example",
            "file//example.com/foo.tar.bz2",
            "garbage",
        )

    @patch('charmhelpers.fetch.plugins')
    def test_installs_remote(self, _plugins):
        h1 = MagicMock(name="h1")
        h1.can_handle.return_value = "Nope"

        h2 = MagicMock(name="h2")
        h2.can_handle.return_value = True
        h2.install.side_effect = fetch.UnhandledSource()

        h3 = MagicMock(name="h3")
        h3.can_handle.return_value = True
        h3.install.return_value = "foo"

        _plugins.return_value = [h1, h2, h3]
        for url in self.valid_urls:
            result = fetch.install_remote(url)

            h1.can_handle.assert_called_with(url)
            h2.can_handle.assert_called_with(url)
            h3.can_handle.assert_called_with(url)

            h1.install.assert_not_called()
            h2.install.assert_called_with(url)
            h3.install.assert_called_with(url)

            self.assertEqual(result, "foo")

    @patch('charmhelpers.fetch.install_remote')
    @patch('charmhelpers.fetch.config')
    def test_installs_from_config(self, _config, _instrem):
        for url in self.valid_urls:
            _config.return_value = {"foo": url}
            fetch.install_from_config("foo")
            _instrem.assert_called_with(url)


class PluginTest(TestCase):
    @patch('charmhelpers.fetch.importlib.import_module')
    def test_imports_plugins(self, import_):
        fetch_handlers = ['a.foo', 'b.foo', 'c.foo']
        module = MagicMock()
        import_.return_value = module
        plugins = fetch.plugins(fetch_handlers)

        self.assertEqual(len(fetch_handlers), len(plugins))
        module.foo.assert_has_calls(([call()] * len(fetch_handlers)))

    @patch('charmhelpers.fetch.importlib.import_module')
    def test_imports_plugins_default(self, import_):
        module = MagicMock()
        import_.return_value = module
        plugins = fetch.plugins()

        self.assertEqual(len(fetch.FETCH_HANDLERS), len(plugins))
        for handler in fetch.FETCH_HANDLERS:
            classname = handler.rsplit('.', 1)[-1]
            getattr(module, classname).assert_called_with()

    @patch('charmhelpers.fetch.log')
    @patch('charmhelpers.fetch.importlib.import_module')
    def test_skips_and_logs_missing_plugins(self, import_, log_):
        fetch_handlers = ['a.foo', 'b.foo', 'c.foo']
        import_.side_effect = (ImportError, AttributeError, MagicMock())
        plugins = fetch.plugins(fetch_handlers)

        self.assertEqual(1, len(plugins))
        self.assertEqual(2, log_.call_count)

    @patch('charmhelpers.fetch.log')
    def test_plugins_are_valid(self, log_):
        plugins = fetch.plugins()
        self.assertEqual(len(fetch.FETCH_HANDLERS), len(plugins))


class BaseFetchHandlerTest(TestCase):

    def setUp(self):
        super(BaseFetchHandlerTest, self).setUp()
        self.test_urls = (
            "http://example.com/foo?bar=baz&x=y#blarg",
            "https://example.com/foo",
            "ftp://example.com/foo",
            "file://example.com/foo",
            "git://github.com/foo/bar",
            "bzr+ssh://bazaar.launchpad.net/foo/bar",
            "bzr+http://bazaar.launchpad.net/foo/bar",
            "garbage",
        )
        self.fh = fetch.BaseFetchHandler()

    def test_handles_nothing(self):
        for url in self.test_urls:
            self.assertNotEqual(self.fh.can_handle(url), True)

    def test_install_throws_unhandled(self):
        for url in self.test_urls:
            self.assertRaises(fetch.UnhandledSource, self.fh.install, url)

    def test_parses_urls(self):
        sample_url = "http://example.com/foo?bar=baz&x=y#blarg"
        p = self.fh.parse_url(sample_url)
        self.assertEqual(p, urlparse(sample_url))

    def test_returns_baseurl(self):
        sample_url = "http://example.com/foo?bar=baz&x=y#blarg"
        expected_url = "http://example.com/foo"
        u = self.fh.base_url(sample_url)
        self.assertEqual(u, expected_url)


class AptTests(TestCase):
    @patch('subprocess.call')
    @patch.object(fetch, 'log')
    def test_installs_apt_packages(self, log, mock_call):
        packages = ['foo', 'bar']
        options = ['--foo', '--bar']

        fetch.apt_install(packages, options)

        mock_call.assert_called_with(['apt-get', '-y', '--foo', '--bar',
                                      'install', 'foo', 'bar'])

    @patch('subprocess.call')
    @patch.object(fetch, 'log')
    def test_installs_apt_packages_without_options(self, log, mock_call):
        packages = ['foo', 'bar']

        fetch.apt_install(packages)

        mock_call.assert_called_with(['apt-get', '-y', 'install', 'foo',
                                      'bar'])

    @patch('subprocess.call')
    @patch.object(fetch, 'log')
    def test_installs_apt_packages_as_string(self, log, mock_call):
        packages = 'foo bar'
        options = ['--foo', '--bar']

        fetch.apt_install(packages, options)

        mock_call.assert_called_with(['apt-get', '-y', '--foo', '--bar',
                                      'install', 'foo bar'])

    @patch('subprocess.check_call')
    @patch.object(fetch, 'log')
    def test_installs_apt_packages_with_possible_errors(self, log, check_call):
        packages = ['foo', 'bar']
        options = ['--foo', '--bar']

        fetch.apt_install(packages, options, fatal=True)

        check_call.assert_called_with(['apt-get', '-y', '--foo', '--bar',
                                       'install', 'foo', 'bar'])


    @patch('subprocess.check_call')
    @patch.object(fetch, 'log')
    def test_purges_apt_packages_as_string_fatal(self, log, mock_call):
        packages = 'irrelevant names'
        mock_call.side_effect = OSError('fail')

        mock_call.assertRaises(OSError, fetch.apt_purge, packages, fatal=True )
        log.assert_called()


    @patch('subprocess.check_call')
    @patch.object(fetch, 'log')
    def test_purges_apt_packages_fatal(self, log, mock_call):
        packages = ['irrelevant', 'names']
        mock_call.side_effect = OSError('fail')

        mock_call.assertRaises(OSError, fetch.apt_purge, packages, fatal=True )
        log.assert_called()


    @patch('subprocess.call')
    @patch.object(fetch, 'log')
    def test_purges_apt_packages_as_string_nofatal(self, log, mock_call):
        packages = 'foo bar'

        fetch.apt_purge(packages)

        log.assert_called()
        mock_call.assert_called_with(['apt-get', '-y', 'purge', 'foo bar'])


    @patch('subprocess.call')
    @patch.object(fetch, 'log')
    def test_purges_apt_packages_nofatal(self, log, mock_call):
        packages = ['foo', 'bar']

        fetch.apt_purge(packages)

        log.assert_called()
        mock_call.assert_called_with(['apt-get', '-y', 'purge', 'foo',
                                      'bar'])


    @patch('subprocess.check_call')
    def test_apt_update_fatal(self, check_call):
        fetch.apt_update(fatal=True)
        check_call.assert_called_with(['apt-get', 'update'])

    @patch('subprocess.call')
    def test_apt_update_nonfatal(self, call):
        fetch.apt_update()
        call.assert_called_with(['apt-get', 'update'])
