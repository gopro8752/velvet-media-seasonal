#!/usr/bin/env python3
"""Run the platform-independent seasonal JUnit slice, not Android/Compose tests.

Requires a JDK and a directory of compiler/runtime/test dependency jars; no network,
SDK stubs, fabricated BuildConfig, or fake native results are used. See README.
"""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tools-dir', required=True, type=Path, help='Directory containing Kotlin compiler and dependency jars')
    args = parser.parse_args()
    java = str(Path(os.environ['JAVA_HOME']) / 'bin/java') if os.environ.get('JAVA_HOME') else shutil.which('java')
    if not java:
        parser.error('Set JAVA_HOME to a JDK or put java on PATH')
    jars = sorted(args.tools_dir.resolve().glob('*.jar'))
    if not any('kotlin-compiler-embeddable' in jar.name for jar in jars):
        parser.error('--tools-dir must contain kotlin-compiler-embeddable and its dependencies')
    classpath = os.pathsep.join(map(str, jars))
    root = Path(__file__).resolve().parents[1]
    main_source = root / 'app/src/main/java/tv/velvet'
    tests = root / 'app/src/test/java/tv/velvet/data/tmdb'
    files = [main_source / name for name in (
        'data/model/Models.kt', 'data/net/HttpAwait.kt', 'data/tmdb/TmdbMetadataRequest.kt',
        'data/tmdb/SeasonalCatalog.kt', 'ui/HomeRowOrder.kt',
    )] + [tests / name for name in ('SeasonalOrderTest.kt', 'TmdbMetadataRequestTest.kt', 'SeasonalCatalogTest.kt')]
    # Compile the actual settings value type, without the adjacent Android DataStore class.
    # Its only default constant is the empty string; this does not exercise DataStore.
    source = (main_source / 'data/settings/SettingsRepository.kt').read_text()
    settings = source[source.index('data class VelvetSettings('):source.index('\nclass SettingsRepository')]
    assert 'const val DEFAULT_SYNC_URL = ""' in source
    settings = settings.replace('SettingsRepository.DEFAULT_SYNC_URL', '""')
    scratch = os.environ.get('TMPDIR')
    if not scratch:
        parser.error('Set TMPDIR to a writable scratch directory outside the repository')
    with tempfile.TemporaryDirectory(prefix='seasonal-jvm-', dir=scratch) as temp:
        output = Path(temp)
        settings_file = output / 'VelvetSettings.kt'
        settings_file.write_text('package tv.velvet.data.settings\nimport tv.velvet.data.model.StreamSourceConfig\n' + settings)
        classes = output / 'classes'
        classes.mkdir()
        subprocess.run([java, '-cp', classpath, 'org.jetbrains.kotlin.cli.jvm.K2JVMCompiler',
            '-no-stdlib', '-no-reflect', '-classpath', classpath, '-d', str(classes),
            *map(str, files), str(settings_file)], check=True)
        subprocess.run([java, '-cp', str(classes) + os.pathsep + classpath, 'org.junit.runner.JUnitCore',
            'tv.velvet.data.tmdb.SeasonalOrderTest', 'tv.velvet.data.tmdb.TmdbMetadataRequestTest',
            'tv.velvet.data.tmdb.SeasonalCatalogTest'], check=True)


if __name__ == '__main__':
    main()
