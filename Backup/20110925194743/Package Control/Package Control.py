# coding=utf-8
import sublime
import sublime_plugin
import os
import sys
import subprocess
import zipfile
import urllib2
import hashlib
import json
import fnmatch
import re
import threading
import datetime
import time
import shutil
import _strptime

try:
    import ssl
except (ImportError):
    pass

class PanelPrinter():
    instance = None

    @classmethod
    def get(cls):
        if cls.instance == None:
            cls.instance = PanelPrinter()
        return cls.instance

    def __init__(self):
        self.name = 'package_control'
        self.window = None
        self.init()

    def init(self):
        if not self.window:
            self.window = sublime.active_window()

        if self.window != None:
            self.panel  = self.window.get_output_panel(self.name)
            self.panel.settings().set("word_wrap", True)
            self.write('Package Control Messages\n========================')

    def show(self):
        sublime.set_timeout(self.show_callback, 10)

    def show_callback(self):
        self.window.run_command("show_panel", {"panel": "output." + self.name})

    def write(self, string):
        callback = lambda: self.write_callback(string)
        sublime.set_timeout(callback, 10)

    def write_callback(self, string):
        self.init()
        self.panel.set_read_only(False)
        edit = self.panel.begin_edit()

        self.panel.insert(edit, self.panel.size(), string)
        self.panel.show(self.panel.size())
        self.panel.end_edit(edit)
        self.panel.set_read_only(True)


class ThreadProgress():
    def __init__(self, thread, message, success_message):
        self.thread = thread
        self.message = message
        self.success_message = success_message
        self.addend = 1
        self.size = 8
        sublime.set_timeout(lambda: self.run(0), 100)

    def run(self, i):
        if not self.thread.is_alive():
            if hasattr(self.thread, 'result') and not self.thread.result:
                sublime.status_message('')
                return
            sublime.status_message(self.success_message)
            return

        before = i % self.size
        after = (self.size - 1) - before
        sublime.status_message('%s [%s=%s]' % \
            (self.message, ' ' * before, ' ' * after))
        if not after:
            self.addend = -1
        if not before:
            self.addend = 1
        i += self.addend
        sublime.set_timeout(lambda: self.run(i), 100)


class ChannelProvider():
    def __init__(self, channel, package_manager):
        self.channel_info = None
        self.channel = channel
        self.package_manager = package_manager

    def match_url(self, url):
        return True

    def fetch_channel(self):
        channel_json = self.package_manager.download_url(self.channel,
            'Error downloading channel.')
        if channel_json == False:
            self.channel_info = False
            return
        try:
            channel_info = json.loads(channel_json)
        except (ValueError):
            sublime.error_message(__name__ + ': Error parsing JSON from ' +
                ' channel ' + self.channel + '.')
            self.channel_info = False
            return
        self.channel_info = channel_info

    def get_name_map(self):
        if self.channel_info == None:
            self.fetch_channel()
        if self.channel_info == False:
            return False
        return self.channel_info['package_name_map']

    def get_repositories(self):
        if self.channel_info == None:
            self.fetch_channel()
        if self.channel_info == False:
            return False
        return self.channel_info['repositories']


_channel_providers = [ChannelProvider]


class PackageProvider():
    def match_url(self, url):
        return True

    def get_packages(self, repo, package_manager):
        repository_json = package_manager.download_url(repo,
            'Error downloading repository.')
        if repository_json == False:
            return False
        try:
            repo_info = json.loads(repository_json)
        except (ValueError):
            sublime.error_message(__name__ + ': Error parsing JSON from ' +
                ' repository ' + repo + '.')
            return False

        identifiers = [sublime.platform() + '-' + sublime.arch(),
            sublime.platform(), '*']
        output = {}
        for package in repo_info['packages']:
            for id in identifiers:
                if not id in package['platforms']:
                    continue

                downloads = []
                for download in package['platforms'][id]:
                    downloads.append(download)

                info = {
                    'name': package['name'],
                    'description': package.get('description'),
                    'url': package.get('homepage', repo),
                    'author': package.get('author'),
                    'downloads': downloads
                }

                output[package['name']] = info
                break
        return output


class GitHubPackageProvider():
    def match_url(self, url):
        return re.search('^https?://github.com/[^/]+/[^/]+$', url) != None

    def get_packages(self, repo, package_manager):
        api_url = re.sub('^https?://github.com/',
            'https://api.github.com/repos/', repo)
        repo_json = package_manager.download_url(api_url,
            'Error downloading repository.')
        if repo_json == False:
            return False
        try:
            repo_info = json.loads(repo_json)
        except (ValueError):
            sublime.error_message(__name__ + ': Error parsing JSON from ' +
                ' repository ' + api_url + '.')
            return False

        commit_date = repo_info['pushed_at']
        timestamp = datetime.datetime.strptime(commit_date[0:19],
            '%Y-%m-%dT%H:%M:%S')
        utc_timestamp = timestamp.strftime(
            '%Y.%m.%d.%H.%M.%S')

        homepage = repo_info['homepage']
        if not homepage:
            homepage = repo_info['html_url']
        package = {
            'name': repo_info['name'],
            'description': repo_info['description'],
            'url': homepage,
            'author': repo_info['owner']['login'],
            'downloads': [
                {
                    'version': utc_timestamp,
                    'url': 'https://nodeload.github.com/' + \
                            repo_info['owner']['login'] + '/' + \
                            repo_info['name'] + '/zipball/master'
                }
            ]
        }
        return {package['name']: package}


class GitHubUserProvider():
    def match_url(self, url):
        return re.search('^https?://github.com/[^/]+$', url) != None

    def get_packages(self, url, package_manager):
        api_url = re.sub('^https?://github.com/',
            'https://api.github.com/users/', url) + '/repos'
        repo_json = package_manager.download_url(api_url,
            'Error downloading repository.')
        if repo_json == False:
            return False
        try:
            repo_info = json.loads(repo_json)
        except (ValueError):
            sublime.error_message(__name__ + ': Error parsing JSON from ' +
                ' repository ' + api_url + '.')
            return False

        packages = {}
        for package_info in repo_info:
            commit_date = package_info['pushed_at']
            timestamp = datetime.datetime.strptime(commit_date[0:19],
                '%Y-%m-%dT%H:%M:%S')
            utc_timestamp = timestamp.strftime(
                '%Y.%m.%d.%H.%M.%S')

            homepage = package_info['homepage']
            if not homepage:
                homepage = package_info['html_url']
            package = {
                'name': package_info['name'],
                'description': package_info['description'],
                'url': homepage,
                'author': package_info['owner']['login'],
                'downloads': [
                    {
                        'version': utc_timestamp,
                        'url': 'https://nodeload.github.com/' + \
                            package_info['owner']['login'] + '/' + \
                            package_info['name'] + '/zipball/master'
                    }
                ]
            }
            packages[package['name']] = package
        return packages


class BitBucketPackageProvider():
    def match_url(self, url):
        return re.search('^https?://bitbucket.org', url) != None

    def get_packages(self, repo, package_manager):
        api_url = re.sub('^https?://bitbucket.org/',
            'https://api.bitbucket.org/1.0/repositories/', repo)
        repo_json = package_manager.download_url(api_url,
            'Error downloading repository.')
        if repo_json == False:
            return False
        try:
            repo_info = json.loads(repo_json)
        except (ValueError):
            sublime.error_message(__name__ + ': Error parsing JSON from ' +
                ' repository ' + api_url + '.')
            return False

        changeset_url = api_url + '/changesets/default'
        changeset_json = package_manager.download_url(changeset_url,
            'Error downloading repository.')
        if changeset_json == False:
            return False
        try:
            last_commit = json.loads(changeset_json)
        except (ValueError):
            sublime.error_message(__name__ + ': Error parsing JSON from ' +
                ' repository ' + changeset_url + '.')
            return False
        commit_date = last_commit['timestamp']
        timestamp = datetime.datetime.strptime(commit_date[0:19],
            '%Y-%m-%d %H:%M:%S')
        utc_timestamp = timestamp.strftime(
            '%Y.%m.%d.%H.%M.%S')

        homepage = repo_info['website']
        if not homepage:
            homepage = repo
        package = {
            'name': repo_info['slug'],
            'description': repo_info['description'],
            'url': homepage,
            'author': repo_info['owner'],
            'downloads': [
                {
                    'version': utc_timestamp,
                    'url': repo + '/get/' + \
                        last_commit['node'] + '.zip'
                }
            ]
        }
        return {package['name']: package}


_package_providers = [BitBucketPackageProvider, GitHubPackageProvider,
    GitHubUserProvider, PackageProvider]


class BinaryNotFoundError(Exception):
    pass


class NonCleanExitError(Exception):
    def __init__(self, returncode):
        self.returncode = returncode

    def __str__(self):
        return repr(self.returncode)


class CliDownloader():
    def __init__(self, settings):
        self.settings = settings

    def find_binary(self, name):
        dirs = ['/usr/local/sbin', '/usr/local/bin', '/usr/sbin', '/usr/bin',
            '/sbin', '/bin']
        for dir in dirs:
            path = os.path.join(dir, name)
            if os.path.exists(path):
                return path

        raise BinaryNotFoundError('The binary ' + name + ' could not be ' + \
            'located')

    def execute(self, args):
        proc = subprocess.Popen(args, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

        output = proc.stdout.read()
        returncode = proc.wait()
        if returncode != 0:
            raise NonCleanExitError(returncode)
        return output



class UrlLib2Downloader():
    def __init__(self, settings):
        self.settings = settings

    def download(self, url, error_message, timeout, tries):
        if self.settings.get('http_proxy') or self.settings.get('https_proxy'):
            proxies = {}
            if self.settings.get('http_proxy'):
                proxies['http'] = self.settings.get('http_proxy')
                if not self.settings.get('https_proxy'):
                    proxies['https'] = self.settings.get('http_proxy')
            if self.settings.get('https_proxy'):
                proxies['https'] = self.settings.get('https_proxy')
            proxy_handler = urllib2.ProxyHandler(proxies)
            urllib2.install_opener(urllib2.build_opener(proxy_handler))

        while tries > 0:
            tries -= 1
            try:
                request = urllib2.Request(url, headers={"User-Agent":
                    "Sublime Package Control"})
                http_file = urllib2.urlopen(request, timeout=timeout)
                return http_file.read()

            except (urllib2.HTTPError) as (e):
                sublime.error_message(__name__ + ': ' + error_message +
                    ' HTTP error ' + str(e.code) + ' downloading ' +
                    url + '.')
            except (urllib2.URLError) as (e):
                # Bitbucket and Github timeout a decent amount
                if str(e.reason) == 'The read operation timed out' or \
                        str(e.reason) == 'timed out':
                    print (__name__ + ': Downloading %s timed out, trying ' + \
                        'again') % url
                    continue
                sublime.error_message(__name__ + ': ' + error_message +
                    ' URL error ' + str(e.reason) + ' downloading ' +
                    url + '.')
            break
        return False


class WgetDownloader(CliDownloader):
    def download(self, url, error_message, timeout, tries):
        wget = self.find_binary('wget')
        if not wget:
            return False
        command = [wget, '--timeout', str(int(timeout)), '-o',
            '/dev/null', '-O', '-', '-U', 'Sublime Package Control', url]

        if self.settings.get('http_proxy'):
            os.putenv('http_proxy', self.settings.get('http_proxy'))
            if not self.settings.get('https_proxy'):
                os.putenv('https_proxy', self.settings.get('http_proxy'))
        if self.settings.get('https_proxy'):
            os.putenv('https_proxy', self.settings.get('https_proxy'))

        while tries > 1:
            tries -= 1
            try:
                return self.execute(command)
            except (NonCleanExitError) as (e):
                if e.returncode == 8:
                    error_string = 'HTTP error 404'
                elif e.returncode == 4:
                    error_string = 'URL error host not found'
                else:
                    # GitHub and BitBucket seem to time out a lot
                    print (__name__ + ': Downloading %s timed out, trying ' + \
                        'again') % url
                    continue
                    #error_string = 'unknown connection error'

                sublime.error_message(__name__ + ': ' + error_message +
                    ' ' + error_string + ' downloading ' +
                    url + '.')
            break
        return False


class CurlDownloader(CliDownloader):
    def download(self, url, error_message, timeout, tries):
        curl = self.find_binary('curl')
        if not curl:
            return False
        command = [curl, '-f', '--user-agent', 'Sublime Package Control',
            '--connect-timeout', str(int(timeout)), '-s', url]

        if self.settings.get('http_proxy'):
            os.putenv('http_proxy', self.settings.get('http_proxy'))
            if not self.settings.get('https_proxy'):
                os.putenv('HTTPS_PROXY', self.settings.get('http_proxy'))
        if self.settings.get('https_proxy'):
            os.putenv('HTTPS_PROXY', self.settings.get('https_proxy'))

        while tries > 1:
            tries -= 1
            try:
                return self.execute(command)
            except (NonCleanExitError) as (e):
                if e.returncode == 22:
                    error_string = 'HTTP error 404'
                elif e.returncode == 6:
                    error_string = 'URL error host not found'
                else:
                    # GitHub and BitBucket seem to time out a lot
                    print (__name__ + ': Downloading %s timed out, trying ' + \
                        'again') % url
                    continue
                    #error_string = 'unknown connection error'

                sublime.error_message(__name__ + ': ' + error_message +
                    ' ' + error_string + ' downloading ' +
                    url + '.')
            break
        return False

_channel_repository_cache = {}

class RepositoryDownloader(threading.Thread):
    def __init__(self, package_manager, name_map, repo):
        self.package_manager = package_manager
        self.repo = repo
        self.packages = {}
        self.name_map = name_map
        threading.Thread.__init__(self)

    def run(self):
        for provider_class in _package_providers:
            provider = provider_class()
            if provider.match_url(self.repo):
                break
        packages = provider.get_packages(self.repo, self.package_manager)
        if packages == False:
            self.packages = False
            return

        mapped_packages = {}
        for package in packages.keys():
            mapped_package = self.name_map.get(package, package)
            mapped_packages[mapped_package] = packages[package]
            mapped_packages[mapped_package]['name'] = mapped_package
        packages = mapped_packages

        self.packages = packages


class VcsUpgrader():
    def __init__(self, vcs_binary, update_command, working_copy, cache_length):
        self.binary = vcs_binary
        self.update_command = update_command
        self.working_copy = working_copy
        self.cache_length = cache_length

    def execute(self, args, dir):
        startupinfo = None
        if os.name == 'nt':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

        proc = subprocess.Popen(args, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            startupinfo=startupinfo, cwd=dir)

        return proc.stdout.read().replace('\r\n', '\n').rstrip(' \n\r')

    def find_binary(self, name):
        if self.binary:
            return self.binary

        if os.name == 'nt':
            dirs = ['C:\\Program Files\\Git\\bin',
                'C:\\Program Files (x86)\\Git\\bin',
                'C:\\Program Files\\TortoiseGit\\bin',
                'C:\\Program Files\\Mercurial',
                'C:\\Program Files (x86)\\Mercurial',
                'C:\\Program Files (x86)\\TortoiseHg',
                'C:\\Program Files\\TortoiseHg',
                'C:\\cygwin\\bin']
        else:
            dirs = ['/usr/local/git/bin', '/usr/local/sbin',
                '/usr/local/bin', '/usr/sbin',
                '/usr/bin', '/sbin', '/bin']

        for dir in dirs:
            path = os.path.join(dir, name)
            if os.path.exists(path):
                return path

        return None


class GitUpgrader(VcsUpgrader):
    def retrieve_binary(self):
        name = 'git'
        if os.name == 'nt':
            name += '.exe'
        binary = self.find_binary(name)
        if binary and os.path.isdir(binary):
            full_path = os.path.join(binary, name)
            if os.path.exists(full_path):
                binary = full_path
        if not binary:
            sublime.error_message(('%s: Unable to find %s. ' +
                'Please set the git_binary setting by accessing the ' +
                'Preferences > Package Settings > %s > ' +
                u'Settings – User menu entry. The Settings – Default entry ' +
                'can be used for reference, but changes to that will be ' +
                'overwritten upon next upgrade.') % (__name__, name, __name__))
            return False

        if os.name == 'nt':
            tortoise_plink = self.find_binary('TortoisePlink.exe')
            if tortoise_plink:
                os.environ.setdefault('GIT_SSH', tortoise_plink)
        return binary

    def run(self):
        binary = self.retrieve_binary()
        if not binary:
            return False
        args = [binary]
        args.extend(self.update_command)
        output = self.execute(args, self.working_copy)
        return True

    def incoming(self):
        cache_key = self.working_copy + '.incoming'
        working_copy_cache = _channel_repository_cache.get(cache_key)
        if working_copy_cache and working_copy_cache.get('time') > \
                time.time():
            return working_copy_cache.get('data')

        binary = self.retrieve_binary()
        if not binary:
            return False
        self.execute([binary, 'fetch'], self.working_copy)
        args = [binary, 'log']
        args.append('..' + '/'.join(self.update_command[-2:]))
        output = self.execute(args, self.working_copy)
        incoming = len(output) > 0

        _channel_repository_cache[cache_key] = {
            'time': time.time() + self.cache_length,
            'data': incoming
        }
        return incoming


class HgUpgrader(VcsUpgrader):
    def retrieve_binary(self):
        name = 'hg'
        if os.name == 'nt':
            name += '.exe'
        binary = self.find_binary(name)
        if binary and os.path.isdir(binary):
            full_path = os.path.join(binary, name)
            if os.path.exists(full_path):
                binary = full_path
        if not binary:
            sublime.error_message(('%s: Unable to find %s. ' +
                'Please set the hg_binary setting by accessing the ' +
                'Preferences > Package Settings > %s > ' +
                u'Settings – User menu entry. The Settings – Default entry ' +
                'can be used for reference, but changes to that will be ' +
                'overwritten upon next upgrade.') % (__name__, name, __name__))
            return False
        return binary

    def run(self):
        binary = self.retrieve_binary()
        if not binary:
            return False
        args = [binary]
        args.extend(self.update_command)
        output = self.execute(args, self.working_copy)
        return True

    def incoming(self):
        cache_key = self.working_copy + '.incoming'
        working_copy_cache = _channel_repository_cache.get(cache_key)
        if working_copy_cache and working_copy_cache.get('time') > \
                time.time():
            return working_copy_cache.get('data')

        binary = self.retrieve_binary()
        if not binary:
            return False
        args = [binary, 'in', '-q']
        args.append(self.update_command[-1])
        output = self.execute(args, self.working_copy)
        incoming = len(output) > 0

        _channel_repository_cache[cache_key] = {
            'time': time.time() + self.cache_length,
            'data': incoming
        }
        return incoming


class PackageManager():
    def __init__(self):
        self.printer = PanelPrinter.get()
        # Here we manually copy the settings since sublime doesn't like
        # code accessing settings from threads
        self.settings = {}
        settings = sublime.load_settings(__name__ + '.sublime-settings')
        for setting in ['timeout', 'repositories', 'repository_channels',
                'package_name_map', 'dirs_to_ignore', 'files_to_ignore',
                'package_destination', 'cache_length', 'auto_upgrade',
                'files_to_ignore_binary', 'files_to_keep', 'dirs_to_keep',
                'git_binary', 'git_update_command', 'hg_binary',
                'hg_update_command', 'http_proxy', 'https_proxy',
                'auto_upgrade_ignore', 'auto_upgrade_frequency']:
            if settings.get(setting) == None:
                continue
            self.settings[setting] = settings.get(setting)

    def compare_versions(self, version1, version2):
        def normalize(v):
            return [int(x) for x in re.sub(r'(\.0+)*$','', v).split(".")]
        return cmp(normalize(version1), normalize(version2))

    def download_url(self, url, error_message):
        has_ssl = 'ssl' in sys.modules
        is_ssl = re.search('^https://', url) != None

        if (is_ssl and has_ssl) or not is_ssl:
            downloader = UrlLib2Downloader(self.settings)
        else:
            for downloader_class in [CurlDownloader, WgetDownloader]:
                try:
                    downloader = downloader_class(self.settings)
                    break
                except (BinaryNotFoundError):
                    pass

        if not downloader:
            sublime.error_message(__name__ + ': Unable to download ' +
                url + ' due to no ssl module available and no capable ' +
                'program found. Please install curl or wget.')
            return False

        timeout = self.settings.get('timeout', 3)
        return downloader.download(url.replace(' ', '%20'), error_message,
            timeout, 3)

    def get_metadata(self, package):
        metadata_filename = os.path.join(self.get_package_dir(package),
            'package-metadata.json')
        if os.path.exists(metadata_filename):
            with open(metadata_filename) as f:
                try:
                    return json.load(f)
                except (ValueError):
                    return {}
        return {}

    def list_repositories(self):
        repositories = self.settings.get('repositories')
        repository_channels = self.settings.get('repository_channels')
        for channel in repository_channels:
            channel_repositories = None

            cache_key = channel + '.repositories'
            repositories_cache = _channel_repository_cache.get(cache_key)
            if repositories_cache and repositories_cache.get('time') > \
                    time.time():
                channel_repositories = repositories_cache.get('data')

            if not channel_repositories:
                for provider_class in _channel_providers:
                    provider = provider_class(channel, self)
                    if provider.match_url(channel):
                        break
                channel_repositories = provider.get_repositories()
                if channel_repositories == False:
                    continue
                _channel_repository_cache[cache_key] = {
                    'time': time.time() + self.settings.get('cache_length',
                        300),
                    'data': channel_repositories
                }
                # Have the local name map override the one from the channel
                name_map = provider.get_name_map()
                name_map.update(self.settings['package_name_map'])
                self.settings['package_name_map'] = name_map

            repositories.extend(channel_repositories)
        return repositories

    def list_available_packages(self):
        repositories = self.list_repositories()
        packages = {}
        downloaders = []
        grouped_downloaders = {}

        # Repositories are run in reverse order so that the ones first
        # on the list will overwrite those last on the list
        for repo in repositories[::-1]:
            repository_packages = None

            cache_key = repo + '.packages'
            packages_cache = _channel_repository_cache.get(cache_key)
            if packages_cache and packages_cache.get('time') > \
                    time.time():
                repository_packages = packages_cache.get('data')
                packages.update(repository_packages)

            if repository_packages == None:
                downloader = RepositoryDownloader(self,
                    self.settings.get('package_name_map', {}), repo)
                domain = re.sub('^https?://[^/]*?(\w+\.\w+)($|/.*$)', '\\1',
                    repo)
                if not grouped_downloaders.get(domain):
                    grouped_downloaders[domain] = []
                grouped_downloaders[domain].append(downloader)

        def schedule(downloader, delay):
            downloader.has_started = False
            def inner():
                downloader.start()
                downloader.has_started = True
            sublime.set_timeout(inner, delay)

        for domain_downloaders in grouped_downloaders.values():
            for i in range(len(domain_downloaders)):
                downloader = domain_downloaders[i]
                downloaders.append(downloader)
                schedule(downloader, i * 150)

        complete = []

        while downloaders:
            downloader = downloaders.pop()
            if downloader.has_started:
                downloader.join()
                complete.append(downloader)
            else:
                downloaders.insert(0, downloader)

        for downloader in complete:
            repository_packages = downloader.packages
            if repository_packages == False:
                continue
            cache_key = downloader.repo + '.packages'
            _channel_repository_cache[cache_key] = {
                'time': time.time() + self.settings.get('cache_length', 300),
                'data': repository_packages
            }
            packages.update(repository_packages)

        return packages

    def list_packages(self):
        package_names = os.listdir(sublime.packages_path())
        package_names = [path for path in package_names if
            os.path.isdir(os.path.join(sublime.packages_path(), path))]
        # Ignore things to be deleted
        ignored_packages = []
        for package in package_names:
            cleanup_file = os.path.join(sublime.packages_path(), package,
                'package-control.cleanup')
            if os.path.exists(cleanup_file):
                ignored_packages.append(package)
        packages = list(set(package_names) - set(ignored_packages) -
            set(self.list_default_packages()))
        packages.sort()
        return packages

    def list_all_packages(self):
        packages = os.listdir(sublime.packages_path())
        packages.sort()
        return packages

    def list_default_packages(self):
        files = os.listdir(os.path.join(os.path.dirname(
            sublime.packages_path()), 'Pristine Packages'))
        files = list(set(files) - set(os.listdir(
            sublime.installed_packages_path())))
        packages = [file.replace('.sublime-package', '') for file in files]
        packages.sort()
        return packages

    def get_package_dir(self, package):
        return os.path.join(sublime.packages_path(), package)

    def get_mapped_name(self, package):
        return self.settings.get('package_name_map', {}).get(package, package)

    def create_package(self, package_name, package_destination,
            binary_package=False):
        package_dir = self.get_package_dir(package_name) + '/'

        if not os.path.exists(package_dir):
            sublime.error_message(__name__ + ': The folder for the ' +
                'package name specified, %s, does not exist in %s' %
                (package_name, sublime.packages_path()))
            return False

        package_filename = package_name + '.sublime-package'
        package_path = os.path.join(package_destination,
            package_filename)

        if not os.path.exists(sublime.installed_packages_path()):
            os.mkdir(sublime.installed_packages_path())

        if os.path.exists(package_path):
            os.remove(package_path)

        try:
            package_file = zipfile.ZipFile(package_path, "w",
                compression=zipfile.ZIP_DEFLATED)
        except (OSError, IOError) as (exception):
            sublime.error_message(__name__ + ': An error occurred ' +
                'creating the package file %s in %s. %s' % (package_filename,
                package_destination, str(exception)))
            return False

        dirs_to_ignore = self.settings.get('dirs_to_ignore', [])
        if not binary_package:
            files_to_ignore = self.settings.get('files_to_ignore', [])
        else:
            files_to_ignore = self.settings.get('files_to_ignore_binary', [])

        package_dir_regex = re.compile('^' + re.escape(package_dir))
        for root, dirs, files in os.walk(package_dir):
            [dirs.remove(dir) for dir in dirs if dir in dirs_to_ignore]
            paths = dirs
            paths.extend(files)
            for path in paths:
                if any(fnmatch.fnmatch(path, pattern) for pattern in
                        files_to_ignore):
                    continue
                full_path = os.path.join(root, path)
                relative_path = re.sub(package_dir_regex, '', full_path)
                if os.path.isdir(full_path):
                    continue
                package_file.write(full_path, relative_path)

        init_script = os.path.join(package_dir, '__init__.py')
        if binary_package and os.path.exists(init_script):
            package_file.write(init_script, re.sub(package_dir_regex, '',
                init_script))
        package_file.close()

        return True

    def install_package(self, package_name):
        installed_packages = self.list_packages()
        packages = self.list_available_packages()

        if package_name not in packages.keys():
            sublime.error_message(__name__ + ': The package specified,' +
                ' %s, is not available.' % (package_name,))
            return False

        download = packages[package_name]['downloads'][0]
        url = download['url']

        package_filename = package_name + \
            '.sublime-package'
        package_path = os.path.join(sublime.installed_packages_path(),
            package_filename)
        pristine_package_path = os.path.join(os.path.dirname(
            sublime.packages_path()), 'Pristine Packages', package_filename)

        package_dir = self.get_package_dir(package_name)

        package_metadata_file = os.path.join(package_dir,
            'package-metadata.json')

        if os.path.exists(os.path.join(package_dir, '.git')):
            return GitUpgrader(self.settings['git_binary'],
                self.settings['git_update_command'], package_dir,
                self.settings['cache_length']).run()
        elif os.path.exists(os.path.join(package_dir, '.hg')):
            return HgUpgrader(self.settings['hg_binary'],
                self.settings['hg_update_command'], package_dir,
                self.settings['cache_length']).run()

        is_upgrade = os.path.exists(package_metadata_file)
        old_version = None
        if is_upgrade:
            old_version = self.get_metadata(package_name).get('version')

        package_bytes = self.download_url(url, 'Error downloading package.')
        if package_bytes == False:
            return False
        with open(package_path, "wb") as package_file:
            package_file.write(package_bytes)

        if not os.path.exists(package_dir):
            os.mkdir(package_dir)

        # We create a backup copy incase something was edited
        else:
            try:
                backup_dir = os.path.join(os.path.dirname(
                    sublime.packages_path()), 'Backup',
                    datetime.datetime.now().strftime('%Y%m%d%H%M%S'))
                if not os.path.exists(backup_dir):
                    os.makedirs(backup_dir)
                package_backup_dir = os.path.join(backup_dir, package_name)
                shutil.copytree(package_dir, package_backup_dir)
            except (OSError, IOError) as (exception):
                sublime.error_message(__name__ + ': An error occurred while' +
                    ' trying to backup the package directory for %s. %s' %
                    (package_name, str(exception)))
                shutil.rmtree(package_backup_dir)
                return False

        # Here we clean out the directory to preven issues with old files
        # however don't just recursively delete the whole package dir since
        # that will fail on Windows if a user has explorer open to it
        def slow_delete(function, path, excinfo):
            if function == os.remove:
                time.sleep(0.2)
                os.remove(path)
        try:
            for path in os.listdir(package_dir):
                full_path = os.path.join(package_dir, path)
                if os.path.isdir(full_path):
                    shutil.rmtree(full_path, onerror=slow_delete)
                else:
                    os.remove(full_path)
        except (OSError, IOError) as (exception):
            sublime.error_message(__name__ + ': An error occurred while' +
                ' trying to remove the package directory for %s. %s' %
                (package_name, str(exception)))
            return False

        package_zip = zipfile.ZipFile(package_path, 'r')
        root_level_paths = []
        last_path = None
        for path in package_zip.namelist():
            last_path = path
            if path.find('/') in [len(path)-1, -1]:
                root_level_paths.append(path)
            if path[0] == '/' or path.find('..') != -1:
                sublime.error_message(__name__ + ': The package ' +
                    'specified, %s, contains files outside of the package ' +
                    'dir and cannot be safely installed.' % (package_name,))
                return False

        if last_path and len(root_level_paths) == 0:
            root_level_paths.append(last_path[0:last_path.find('/')+1])

        os.chdir(package_dir)

        # Here we don’t use .extractall() since it was having issues on OS X
        skip_root_dir = len(root_level_paths) == 1 and \
            root_level_paths[0].endswith('/')
        for path in package_zip.namelist():
            dest = path
            if os.name == 'nt':
                regex = ':|\*|\?|"|<|>|\|'
                if re.search(regex, dest) != None:
                    print ('%s: Skipping file from package ' +
                        'named %s due to an invalid filename') % (__name__,
                        path)
                    continue
            regex = '[\x00-\x1F\x7F-\xFF]'
            if re.search(regex, dest) != None:
                dest = dest.decode('utf-8')
            # If there was only a single directory in the package, we remove
            # that folder name from the paths as we extract entries
            if skip_root_dir:
                dest = dest[len(root_level_paths[0]):]
            dest = os.path.join(package_dir, dest)
            if path.endswith('/'):
                if not os.path.exists(dest):
                    os.makedirs(dest)
            else:
                dest_dir = os.path.dirname(dest)
                if not os.path.exists(dest_dir):
                    os.makedirs(dest_dir)
                try:
                    open(dest, 'wb').write(package_zip.read(path))
                except (IOError, UnicodeDecodeError):
                    print ('%s: Skipping file from package ' +
                        'named %s due to an invalid filename') % (__name__,
                        path)
        package_zip.close()

        self.print_messages(package_name, package_dir, is_upgrade, old_version)

        with open(package_metadata_file, 'w') as f:
            metadata = {
                "version": packages[package_name]['downloads'][0]['version'],
                "url": packages[package_name]['url'],
                "description": packages[package_name]['description']
            }
            json.dump(metadata, f)

        # Here we delete the package file from the installed packages directory
        # since we don't want to accidentally overwrite user changes
        os.remove(package_path)
        # We have to remove the pristine package too or else Sublime Text 2
        # will silently delete the package
        if os.path.exists(pristine_package_path):
            os.remove(pristine_package_path)

        os.chdir(sublime.packages_path())
        return True

    def print_messages(self, package, package_dir, is_upgrade, old_version):
        messages_file = os.path.join(package_dir, 'messages.json')
        if os.path.exists(messages_file):
            messages_fp = open(messages_file, 'r')
            message_info = json.load(messages_fp)
            messages_fp.close()

            shown = False
            if not is_upgrade and message_info.get('install'):
                install_messages = os.path.join(package_dir,
                    message_info.get('install'))
                message = '\n\n' + package + ':\n  '
                with open(install_messages, 'r') as f:
                    message += f.read().replace('\n', '\n  ')
                self.printer.write(message)
                shown = True

            elif is_upgrade and old_version:
                upgrade_messages = list(set(message_info.keys()) -
                    set(['install']))
                upgrade_messages = sorted(upgrade_messages,
                    cmp=self.compare_versions, reverse=True)
                for version in upgrade_messages:
                    if self.compare_versions(old_version, version) >= 0:
                        break
                    if not shown:
                        message = '\n\n' + package + ':'
                        self.printer.write(message)
                    upgrade_messages = os.path.join(package_dir,
                        message_info.get(version))
                    message = '\n  '
                    with open(upgrade_messages, 'r') as f:
                        message += f.read().replace('\n', '\n  ')
                    self.printer.write(message)
                    shown = True

            if shown:
                self.printer.show()

    def remove_package(self, package_name):
        installed_packages = self.list_packages()

        if package_name not in installed_packages:
            sublime.error_message(__name__ + ': The package specified,' +
                ' %s, is not installed.' % (package_name,))
            return False

        os.chdir(sublime.packages_path())

        # Give Sublime Text some time to ignore the package
        time.sleep(1)

        package_filename = package_name + '.sublime-package'
        package_path = os.path.join(sublime.installed_packages_path(),
            package_filename)
        installed_package_path = os.path.join(os.path.dirname(
            sublime.packages_path()), 'Installed Packages', package_filename)
        pristine_package_path = os.path.join(os.path.dirname(
            sublime.packages_path()), 'Pristine Packages', package_filename)
        package_dir = self.get_package_dir(package_name)

        try:
            if os.path.exists(package_path):
                os.remove(package_path)
        except (OSError, IOError) as (exception):
            sublime.error_message(__name__ + ': An error occurred while' +
                ' trying to remove the package file for %s. %s' %
                (package_name, str(exception)))
            return False

        try:
            if os.path.exists(installed_package_path):
                os.remove(installed_package_path)
        except (OSError, IOError) as (exception):
            sublime.error_message(__name__ + ': An error occurred while' +
                ' trying to remove the installed package file for %s. %s' %
                (package_name, str(exception)))
            return False

        try:
            if os.path.exists(pristine_package_path):
                os.remove(pristine_package_path)
        except (OSError, IOError) as (exception):
            sublime.error_message(__name__ + ': An error occurred while' +
                ' trying to remove the pristine package file for %s. %s' %
                (package_name, str(exception)))
            return False

        # We don't delete the actual package dir immediately due to a bug
        # in sublime_plugin.py
        can_delete_dir = True
        for path in os.listdir(package_dir):
            try:
                full_path = os.path.join(package_dir, path)
                if not os.path.isdir(full_path):
                    os.remove(full_path)
                else:
                    shutil.rmtree(full_path)
            except (OSError, IOError) as (exception):
                # If there is an error deleting now, we will mark it for
                # cleanup the next time Sublime Text starts
                open(os.path.join(package_dir, 'package-control.cleanup'),
                    'w').close()
                can_delete_dir = False

        if can_delete_dir:
            os.rmdir(package_dir)

        return True


class PackageCreator():
    def show_panel(self):
        self.manager = PackageManager()
        self.packages = self.manager.list_packages()
        if not self.packages:
            sublime.error_message(__name__ + ': There are no packages ' +
                'available to be packaged.')
            return
        self.window.show_quick_panel(self.packages, self.on_done)

    def get_package_destination(self):
        destination = self.manager.settings.get('package_destination')

        # We check destination via an if statement instead of using
        # the dict.get() method since the key may be set, but to a blank value
        if not destination:
            destination = os.path.join(os.path.expanduser('~'),
                'Desktop')

        return destination


class CreatePackageCommand(sublime_plugin.WindowCommand, PackageCreator):
    def run(self):
        self.show_panel()

    def on_done(self, picked):
        if picked == -1:
            return
        package_name = self.packages[picked]
        package_destination = self.get_package_destination()

        if self.manager.create_package(package_name, package_destination):
            self.window.run_command('open_dir', {"dir":
                package_destination, "file": package_name +
                '.sublime-package'})


class CreateBinaryPackageCommand(sublime_plugin.WindowCommand, PackageCreator):
    def run(self):
        self.show_panel()

    def on_done(self, picked):
        if picked == -1:
            return
        package_name = self.packages[picked]
        package_destination = self.get_package_destination()

        if self.manager.create_package(package_name, package_destination,
                binary_package=True):
            self.window.run_command('open_dir', {"dir":
                package_destination, "file": package_name +
                '.sublime-package'})


class PackageInstaller():
    def __init__(self):
        self.manager = PackageManager()

    def make_package_list(self, ignore_actions=[], override_action=None,
            ignore_packages=[]):
        packages = self.manager.list_available_packages()
        installed_packages = self.manager.list_packages()

        package_list = []
        for package in sorted(packages.iterkeys()):
            if ignore_packages and package in ignore_packages:
                continue
            package_entry = [package]
            info = packages[package]
            download = info['downloads'][0]

            if package in installed_packages:
                installed = True
                metadata = self.manager.get_metadata(package)
                if metadata.get('version'):
                    installed_version = metadata['version']
                else:
                    installed_version = None
            else:
                installed = False

            installed_version_name = 'v' + installed_version if \
                installed and installed_version else 'unknown version'
            new_version = 'v' + download['version']

            vcs = None
            package_dir = self.manager.get_package_dir(package)
            settings = self.manager.settings

            if override_action:
                action = override_action
                extra = ''

            else:
                if os.path.exists(os.path.join(sublime.packages_path(), package,
                        '.git')):
                    vcs = 'git'
                    incoming = GitUpgrader(settings.get('git_binary'),
                        settings.get('git_update_command'), package_dir,
                        settings.get('cache_length')).incoming()
                elif os.path.exists(os.path.join(sublime.packages_path(), package,
                        '.hg')):
                    vcs = 'hg'
                    incoming = HgUpgrader(settings.get('hg_binary'),
                        settings.get('hg_update_command'), package_dir,
                        settings.get('cache_length')).incoming()

                if installed:
                    if not installed_version:
                        if vcs:
                            if incoming:
                                action = 'pull'
                                extra = ' with ' + vcs
                            else:
                                action = 'none'
                                extra = ''
                        else:
                            action = 'overwrite'
                            extra = ' %s with %s' % (installed_version_name,
                                new_version)
                    else:
                        res = self.manager.compare_versions(
                            installed_version, download['version'])
                        if res < 0:
                            action = 'upgrade'
                            extra = ' to %s from %s' % (new_version,
                                installed_version_name)
                        elif res > 0:
                            action = 'downgrade'
                            extra = ' to %s from %s' % (new_version,
                                installed_version_name)
                        else:
                            action = 'reinstall'
                            extra = ' %s' % new_version
                else:
                    action = 'install'
                    extra = ' %s' % new_version
                extra += ';'

                if action in ignore_actions:
                    continue

            package_entry.append(info.get('description', 'No description ' + \
                'provided'))
            package_entry.append(action + extra + ' ' +
                re.sub('^https?://', '', info['url']))
            package_list.append(package_entry)
        return package_list

    def on_done(self, picked):
        if picked == -1:
            return
        name = self.package_list[picked][0]
        thread = PackageInstallerThread(self.manager, name)
        thread.start()
        ThreadProgress(thread, 'Installing package %s' % name,
            'Package %s successfully %s' % (name, self.completion_type))


class PackageInstallerThread(threading.Thread):
    def __init__(self, manager, package):
        self.package = package
        self.manager = manager
        threading.Thread.__init__(self)

    def run(self):
        self.result = self.manager.install_package(self.package)


class InstallPackageCommand(sublime_plugin.WindowCommand):
    def run(self):
        thread = InstallPackageThread(self.window)
        thread.start()
        ThreadProgress(thread, 'Loading repositories', '')


class InstallPackageThread(threading.Thread, PackageInstaller):
    def __init__(self, window):
        self.window = window
        self.completion_type = 'installed'
        threading.Thread.__init__(self)
        PackageInstaller.__init__(self)

    def run(self):
        self.package_list = self.make_package_list(['upgrade', 'downgrade',
            'reinstall', 'pull', 'none'])
        def show_quick_panel():
            if not self.package_list:
                sublime.error_message(__name__ + ': There are no packages ' +
                    'available for installation.')
                return
            self.window.show_quick_panel(self.package_list, self.on_done)
        sublime.set_timeout(show_quick_panel, 10)


class DiscoverPackagesCommand(sublime_plugin.WindowCommand):
    def run(self):
        thread = DiscoverPackagesThread(self.window)
        thread.start()
        ThreadProgress(thread, 'Loading repositories', '')


class DiscoverPackagesThread(threading.Thread, PackageInstaller):
    def __init__(self, window):
        self.window = window
        self.completion_type = 'installed'
        threading.Thread.__init__(self)
        PackageInstaller.__init__(self)

    def run(self):
        self.package_list = self.make_package_list(override_action='visit')
        def show_quick_panel():
            if not self.package_list:
                sublime.error_message(__name__ + ': There are no packages ' +
                    'available for discovery.')
                return
            self.window.show_quick_panel(self.package_list, self.on_done)
        sublime.set_timeout(show_quick_panel, 10)

    def on_done(self, picked):
        if picked == -1:
            return
        package_name = self.package_list[picked][0]
        packages = self.manager.list_available_packages()
        def open_url():
            sublime.active_window().run_command('open_url',
                {"url": packages.get(package_name).get('url')})
        sublime.set_timeout(open_url, 10)


class UpgradePackageCommand(sublime_plugin.WindowCommand):
    def run(self):
        thread = UpgradePackageThread(self.window)
        thread.start()
        ThreadProgress(thread, 'Loading repositories', '')


class UpgradePackageThread(threading.Thread, PackageInstaller):
    def __init__(self, window):
        self.window = window
        self.completion_type = 'upgraded'
        threading.Thread.__init__(self)
        PackageInstaller.__init__(self)

    def run(self):
        self.package_list = self.make_package_list(['install', 'reinstall',
            'none'])
        def show_quick_panel():
            if not self.package_list:
                sublime.error_message(__name__ + ': There are no packages ' +
                    'ready for upgrade.')
                return
            self.window.show_quick_panel(self.package_list, self.on_done)
        sublime.set_timeout(show_quick_panel, 10)

    def on_done(self, picked):
        if picked == -1:
            return
        name = self.package_list[picked][0]
        thread = PackageInstallerThread(self.manager, name)
        thread.start()
        ThreadProgress(thread, 'Upgrading package %s' % name,
            'Package %s successfully %s' % (name, self.completion_type))


class UpgradeAllPackagesCommand(sublime_plugin.WindowCommand):
    def run(self):
        thread = UpgradeAllPackagesThread(self.window)
        thread.start()
        ThreadProgress(thread, 'Loading repositories', '')


class UpgradeAllPackagesThread(threading.Thread, PackageInstaller):
    def __init__(self, window):
        self.window = window
        self.completion_type = 'upgraded'
        threading.Thread.__init__(self)
        PackageInstaller.__init__(self)

    def run(self):
        for info in self.make_package_list(['install', 'reinstall', 'none']):
            thread = PackageInstallerThread(self.manager, info[0])
            thread.start()
            ThreadProgress(thread, 'Upgrading package %s' % info[0],
                'Package %s successfully %s' % (info[0], self.completion_type))


class ExistingPackagesCommand():
    def __init__(self):
        self.manager = PackageManager()

    def make_package_list(self, action=''):
        packages = self.manager.list_packages()

        if action:
            action += ' '

        package_list = []
        for package in sorted(packages):
            package_entry = [package]
            metadata = self.manager.get_metadata(package)
            package_dir = os.path.join(sublime.packages_path(), package)

            package_entry.append(metadata.get('description',
                'No description provided'))

            version = metadata.get('version')
            if not version and os.path.exists(os.path.join(package_dir, '.git')):
                installed_version = 'git repository'
            elif not version and os.path.exists(os.path.join(package_dir, '.hg')):
                installed_version = 'hg repository'
            else:
                installed_version = 'v' + version if version else 'unknown version'

            url = metadata.get('url')
            if url:
                url = '; ' + re.sub('^https?://', '', url)
            else:
                url = ''

            package_entry.append(action + installed_version + url)
            package_list.append(package_entry)

        return package_list


class ListPackagesCommand(sublime_plugin.WindowCommand):
    def run(self):
        ListPackagesThread(self.window).start()


class ListPackagesThread(threading.Thread, ExistingPackagesCommand):
    def __init__(self, window):
        self.window = window
        threading.Thread.__init__(self)
        ExistingPackagesCommand.__init__(self)

    def run(self):
        self.package_list = self.make_package_list()

        def show_quick_panel():
            if not self.package_list:
                sublime.error_message(__name__ + ': There are no packages ' +
                    'to list.')
                return
            self.window.show_quick_panel(self.package_list, self.on_done)
        sublime.set_timeout(show_quick_panel, 10)

    def on_done(self, picked):
        if picked == -1:
            return
        package_name = self.package_list[picked][0]
        def open_dir():
            self.window.run_command('open_dir',
                {"dir": os.path.join(sublime.packages_path(), package_name)})
        sublime.set_timeout(open_dir, 10)


class RemovePackageCommand(sublime_plugin.WindowCommand,
        ExistingPackagesCommand):
    def __init__(self, window):
        self.window = window
        ExistingPackagesCommand.__init__(self)

    def run(self):
        self.package_list = self.make_package_list('remove')
        if not self.package_list:
            sublime.error_message(__name__ + ': There are no packages ' +
                'that can be removed.')
            return
        self.window.show_quick_panel(self.package_list, self.on_done)

    def on_done(self, picked):
        if picked == -1:
            return
        package = self.package_list[picked][0]
        settings = sublime.load_settings('Global.sublime-settings')
        ignored_packages = settings.get('ignored_packages')
        if not ignored_packages:
            ignored_packages = []
        if not package in ignored_packages:
            ignored_packages.append(package)
            settings.set('ignored_packages', ignored_packages)
            sublime.save_settings('Global.sublime-settings')

        ignored_packages.remove(package)
        thread = RemovePackageThread(self.manager, package,
            ignored_packages)
        thread.start()
        ThreadProgress(thread, 'Removing package %s' % package,
            'Package %s successfully removed' % package)


class RemovePackageThread(threading.Thread):
    def __init__(self, manager, package, ignored_packages):
        self.manager = manager
        self.package = package
        self.ignored_packages = ignored_packages
        threading.Thread.__init__(self)

    def run(self):
        self.result = self.manager.remove_package(self.package)
        def unignore_package():
            settings = sublime.load_settings('Global.sublime-settings')
            settings.set('ignored_packages', self.ignored_packages)
            sublime.save_settings('Global.sublime-settings')
        sublime.set_timeout(unignore_package, 10)


class AddRepositoryChannelCommand(sublime_plugin.WindowCommand):
    def run(self):
        self.window.show_input_panel('Repository Channel JSON URL', '',
            self.on_done, self.on_change, self.on_cancel)

    def on_done(self, input):
        settings = sublime.load_settings(__name__ + '.sublime-settings')
        repository_channels = settings.get('repository_channels', [])
        if not repository_channels:
            repository_channels = []
        repository_channels.append(input)
        settings.set('repository_channels', repository_channels)
        sublime.save_settings(__name__ + '.sublime-settings')
        sublime.status_message('Repository channel ' + input +
            ' successfully added')

    def on_change(self, input):
        pass

    def on_cancel(self):
        pass


class AddRepositoryCommand(sublime_plugin.WindowCommand):
    def run(self):
        self.window.show_input_panel('GitHub or BitBucket Web URL, or Custom JSON Repository URL', '', self.on_done,
            self.on_change, self.on_cancel)

    def on_done(self, input):
        settings = sublime.load_settings(__name__ + '.sublime-settings')
        repositories = settings.get('repositories', [])
        if not repositories:
            repositories = []
        repositories.append(input)
        settings.set('repositories', repositories)
        sublime.save_settings(__name__ + '.sublime-settings')
        sublime.status_message('Repository ' + input + ' successfully added')

    def on_change(self, input):
        pass

    def on_cancel(self):
        pass


class DisablePackageCommand(sublime_plugin.WindowCommand):
    def run(self):
        manager = PackageManager()
        packages = manager.list_all_packages()
        self.settings = sublime.load_settings('Global.sublime-settings')
        disabled_packages = self.settings.get('ignored_packages')
        if not disabled_packages:
            disabled_packages = []
        self.package_list = list(set(packages) - set(disabled_packages))
        self.package_list.sort()
        if not self.package_list:
            sublime.error_message(__name__ + ': There are no enabled ' +
            'packages to disable.')
            return
        self.window.show_quick_panel(self.package_list, self.on_done)

    def on_done(self, picked):
        if picked == -1:
            return
        package = self.package_list[picked]
        ignored_packages = self.settings.get('ignored_packages')
        if not ignored_packages:
            ignored_packages = []
        ignored_packages.append(package)
        self.settings.set('ignored_packages', ignored_packages)
        sublime.save_settings('Global.sublime-settings')
        sublime.status_message('Package ' + package + ' successfully added ' +
            'to list of diabled packges - restarting Sublime Text may be '
            'required')


class EnablePackageCommand(sublime_plugin.WindowCommand):
    def run(self):
        self.settings = sublime.load_settings('Global.sublime-settings')
        self.disabled_packages = self.settings.get('ignored_packages')
        self.disabled_packages.sort()
        if not self.disabled_packages:
            sublime.error_message(__name__ + ': There are no disabled ' +
            'packages to enable.')
            return
        self.window.show_quick_panel(self.disabled_packages, self.on_done)

    def on_done(self, picked):
        if picked == -1:
            return
        package = self.disabled_packages[picked]
        ignored = self.settings.get('ignored_packages')
        self.settings.set('ignored_packages',
            list(set(ignored) - set([package])))
        sublime.save_settings('Global.sublime-settings')
        sublime.status_message('Package ' + package + ' successfully removed' +
            ' from list of diabled packages - restarting Sublime Text may be '
            'required')


class AutomaticUpgrader(threading.Thread):
    def __init__(self):
        self.installer = PackageInstaller()

        settings = sublime.load_settings(__name__ + '.sublime-settings')
        self.auto_upgrade = settings.get('auto_upgrade')
        self.auto_upgrade_ignore = settings.get('auto_upgrade_ignore')

        self.next_run = int(time.time())
        self.last_run = settings.get('auto_upgrade_last_run')
        frequency = settings.get('auto_upgrade_frequency')
        if frequency:
            if self.last_run:
                self.next_run = int(self.last_run) + (frequency * 60 * 60)
            else:
                self.next_run = time.time()

        if self.auto_upgrade and self.next_run <= time.time():
            settings.set('auto_upgrade_last_run', int(time.time()))
            sublime.save_settings(__name__ + '.sublime-settings')

        threading.Thread.__init__(self)

    def run(self):
        if self.next_run > time.time():
            last_run = datetime.datetime.fromtimestamp(self.last_run)
            next_run = datetime.datetime.fromtimestamp(self.next_run)
            date_format = '%Y-%m-%d %H:%M:%S'
            print (__name__ + ': Skipping automatic upgrade, last run at ' +
                '%s, next run at %s or after') % (last_run.strftime(
                    date_format), next_run.strftime(date_format))
            return

        if self.auto_upgrade:
            packages = self.installer.make_package_list(['install',
                'reinstall', 'downgrade', 'overwrite', 'none'],
                ignore_packages=self.auto_upgrade_ignore)

            if not packages:
                print __name__ + ': No updated packages'
                return

            print __name__ + ': Installing %s upgrades' % len(packages)
            for package in packages:
                self.installer.manager.install_package(package[0])
                version = re.sub('^.*?(v[\d\.]+).*?$', '\\1', package[2])
                if version == package[2] and version.find('pull with') != -1:
                    vcs = re.sub('^pull with (\w+).*?$', '\\1', version)
                    version = 'latest %s commit' % vcs
                print __name__ + ': Upgraded %s to %s' % (package[0], version)


class PackageCleanup(threading.Thread):
    def __init__(self):
        threading.Thread.__init__(self)

    def run(self):
        for path in os.listdir(sublime.packages_path()):
            package_dir = os.path.join(sublime.packages_path(), path)
            if os.path.exists(os.path.join(package_dir,
                    'package-control.cleanup')):
                shutil.rmtree(package_dir)
                print __name__ + ': Removed old directory for package %s' % \
                    path
        sublime.set_timeout(lambda: AutomaticUpgrader().start(), 10)


PackageCleanup().start()