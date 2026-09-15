# piixesicons

## Project Status

An icon archive and Python scraper. The scraper discovers icon slugs, downloads image bytes and keeps local discovery, completion and failure files for later runs. Retained assets remain in the repository; this maintenance release does not migrate them to another storage provider.

## Setup

Use Python 3.12 and install the existing pinned dependencies:

```sh
python -m pip install -r requirements.txt
```

## Usage

```sh
python scrape_images.py
```

Enter the output folder and cookie locally when prompted. Keep cookies out of source control. Run one scraper process per output folder; its eight download workers share a lock for local file publication.

Images are stored under `<output>/piixes.com/api/icon/512/`. A successful download publishes the complete image through a flushed temporary file, then atomically records completion before updating in-memory duplicate tracking. Local write retries reuse the received bytes. Identical responses retain a file and completion for every slug; `dedup` counts repeated content and no longer means a missing slug file.

An existing incomplete output is compared with the downloaded bytes. Matching bytes repair a missing completion entry; differing bytes are preserved and reported as a failure for review. Completion entries whose files are missing are retried. Existing completed files are not automatically revalidated, and this release does not audit the integrity of the retained archive.

Completion updates preserve the previous checkpoint bytes, including duplicate entries. Atomic replacement rewrites that small text file for each newly completed slug, adding local disk work. The existing per-request timeout, four-attempt limit and eight workers remain. Discovery still needs a separate overall request/time budget; the offline checks below do not scrape the live service.

## Offline validation

```sh
python -m unittest discover -s tests -v
```

The tests use synthetic responses and disposable directories. They cover interrupted writes, checkpoint failures, retries, duplicate content, concurrent workers and missing-file recovery without accessing retained images, real cookies or the provider. The Python Actions job checks out only the source, dependency file and tests.

## License

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
