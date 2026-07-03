# CHANGELOG

<!-- version list -->

## v1.1.0 (2026-07-03)

### Bug Fixes

- Add continue-on-error to codecov step to prevent job failure on upload issues
  ([`a5c6be4`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/a5c6be4e5d5426d1895d0db3870c08e0b37a7fcd))

- Add percent-encoding note for URL-shaped IDs on reset-credibility endpoint
  ([`c9abbc3`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/c9abbc38388b4f5118537fa8723eb3900f79a014))

- Address Copilot PR review comments (role guard, list content_len, stale comment)
  ([`6b36daf`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/6b36daf077d3d0bb1dfbfc42ee47056b85c4bd24))

- COALESCE credibility_score in Postgres adjust_credibility; fix document_id type in description
  ([`aeceb0d`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/aeceb0dc969c85812e437060c722e9321a42eb08))

- Correct reset-credibility route paths and doc errors in wiki
  ([`6d29ac5`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/6d29ac5a0be273f435997c205f68d3631d4ccde8))

- Gate rated-feedback credibility signal to APPROVED-only (reject Copilot review)
  ([`7ffb1d2`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/7ffb1d2362f7d6b8900b9c55badbd15f08ac0fd6))

- Guard cited-doc override to source='feedback'; clarify citation_boost docstring
  ([`e7e3511`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/e7e3511db285b570d4fddd21822992217021b452))

- Guard non-dict tool_results entries in content mirror regen and debug logging
  ([`87c020c`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/87c020c957c40ae424b1f5061fdd821704381f66))

- Update test_metadata_kb_path to expect document_id key in kb_sources
  ([`c87b097`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/c87b09786f513d13db92f95e4137442cf71bd2bd))

- Use 'files' instead of 'file' for codecov-action@v7
  ([`b2a8584`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/b2a8584619c54c6c47aacd1f5e141581021292bc))

- Use _get_tool_result_payload in content mirror regen; fix stale comment in preprocess.py
  ([`8ad3a15`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/8ad3a15b91e173a961dc13617c255b4a756f7d75))

- Wrap adjust_credibility in asyncio.to_thread; deduplicate doc_ids in feedback signal
  ([`362b5e1`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/362b5e1ef631829927bf574b670aa7d75101d739))

- **admin-panel**: Make tag filters case-insensitive and auto-apply
  ([`e6d2a97`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/e6d2a97c34847419f7d90380fdd1d579fe8473d9))

- **chat-ui**: Restore textarea focus after response unlock
  ([`56f2463`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/56f24636d6a454506b835f0b8477170ff789b793))

- **deps**: Regenerate poetry.lock to match pyproject.toml
  ([`006ad3f`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/006ad3fbce53bb80981d338188038d9e7960182e))

- **feedback**: Atomic conditional delete to close TOCTOU gap
  ([`8c4acae`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/8c4acae0f0757dcdf60be499e322544623d604cb))

- **feedback**: Restore null guard in enrichment url validator
  ([`c2e92bf`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/c2e92bfedab13cf735d1088906f26b9ef7311de1))

### Chores

- Changed default behaviour of the feedback metadata, hidden by default
  ([`387db96`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/387db9656792b1d9f3d8c21072ad6c0274e2cfea))

- Move python-semantic-release to dev dependency group
  ([`82fb9e0`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/82fb9e0d1ce7c3a5f7b2eb14319d26bab2b3baa0))

- Remove autochat_feedback_metadata_enrichment_max_bytes var and logic
  ([`567b457`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/567b4575b0d2b8dc00ef51d25d639469e836605e))

- Remove dead code, deployment.py
  ([`b0a0f1a`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/b0a0f1a7fea96138f8879265a24a1e72ea929f6b))

- **deps**: Sync requirements.txt from poetry.lock
  ([`d59c67e`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/d59c67e142c7ccd1fec10e2bd105f34945275fe5))

- **deps**: Sync requirements.txt from poetry.lock
  ([`6571864`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/6571864d7f721b5392d8e09133416f419346b914))

- **deps**: Sync requirements.txt from poetry.lock
  ([`a55e5f6`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/a55e5f6a76b9505a7b4715e78a96d941e49857ae))

- **deps**: Update github-actions-updates
  ([`0f7cf79`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/0f7cf7920706d13e03f632d2704debaf94cc7147))

- **docs/tests**: Added websocket client documentation and unit test
  ([`b72b142`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/b72b14290a2801d65215f9649f49b1fcc5bb8de3))

### Code Style

- Apply pre-commit autofixes (flake8 noqa, prettier)
  ([`88210be`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/88210be6ad1fcb6ce1089b8ebf7e62606387a0cd))

- Fix black lint issues.
  ([`2974c1b`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/2974c1be95fdc749d653fd8e84c87429b003d62d))

- **feedback**: Apply black formatting
  ([`6fc3bb0`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/6fc3bb02a679d7df777eaac88effd443576d8e92))

### Continuous Integration

- **release**: Use default GITHUB_TOKEN for checkout fetch
  ([`31c7c12`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/31c7c1256cf5bd2975a6b059a9b1091981b0a8a3))

- **release**: Use GH_TOKEN secret for checkout and semantic-release
  ([`83af92e`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/83af92e08edaf214547a6a7396ce178c75b2bc89))

### Features

- **admin-feedback**: Add hard-delete for rejected feedback entries
  ([`8d6f004`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/8d6f0041f02c9898452b726fc1d504fb94f4912a))

- **dashboard**: Bulk-delete rejected feedback via row checkboxes
  ([`a01fc40`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/a01fc4067e7e4aeca7f1fefb8de0469e2eb43592))

- **dashboard**: Show feedback metadata drawer section
  ([`f6fbe96`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/f6fbe96a4242d397f0ef4d33aff6efb081c525e3))

- **feedback**: Add pluggable metadata enrichment endpoint
  ([`3cdf789`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/3cdf789dc4960f426a98f0c936795f9e3c6adfc8))

- **plugin**: Make startup()/shutdown() idempotent, document lifespan pattern
  ([`fdb0b98`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/fdb0b981ebf8137057052ff20ba93299026698e8))

### Refactoring

- **plugin**: Wire startup/shutdown into lifespan, drop on_event
  ([`82d501c`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/82d501cbebe8149b41fa3c0e36086262dc7f7f35))

### Testing

- Add negative-rated APPROVED signal test and citation_boost_node unit tests
  ([`bb3839d`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/bb3839d71416e1e46076e3fe91cb5ffd488ff5c8))

- **feedback**: Scope package stubs to test run to avoid sys.modules leak
  ([`6adc47d`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/6adc47da463979428f3f62d9cc4080893b5a535d))

- **isolation**: Restore sys.modules in load_module to avoid leaks
  ([`679b1c8`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/679b1c814fa1dd093ebbcf720500af8487ba18ee))

- **websocket**: Drop leaked autolangchat stubs before importing handler
  ([`15c3f78`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/15c3f782308efcfc260dd995ae6e524190d4abdd))


## v1.0.0 (2026-06-23)

### Chores

- **cicd**: Remove unused workflows
  ([`2b07fb2`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/2b07fb2c40f071471ed03f56233994d383769000))

- **deps**: Sync requirements.txt from poetry.lock
  ([`62fa4cb`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/62fa4cb787681dcab068f5e675f2982f76d571f0))

### Features

- **dashboard**: Display full conversation history with markdown rendering
  ([`92ac3e8`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/92ac3e8e4d49d4c4fab491fb9ccedf04f176e990))


## v0.3.0 (2026-06-12)

### Bug Fixes

- **tests**: Normalize whitespace in pgvector schema SQL assertion
  ([`cf86586`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/cf865864392a259271685e5ecf1228f617186b06))

- **websocket**: Add future annotations to resolve SSOSessionStore NameError
  ([`60ef03b`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/60ef03b88f2b4480347b5e9bdd46a10d07aa6435))

### Chores

- Changed from base python3.14 to python3.14-slim.
  ([`637a90f`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/637a90f173e8ec667af7436dfd5f2e35f9650d8f))

- Downgrade python version from 3.15 to 3.14
  ([`35a1bea`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/35a1bea7bc76c4b898d90274d4a9caa812c7ee74))

- **deps**: Sync requirements.txt from poetry.lock
  ([`92df3f9`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/92df3f900bd81417dc9c6e9e4f3c05e234c189da))

- **deps**: Sync requirements.txt from poetry.lock
  ([`31823cd`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/31823cd6dd80eaa3eb608ced248fd7ca2dfe3c3e))

- **deps**: Sync requirements.txt from poetry.lock
  ([`481e1e4`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/481e1e4237332e9410a14f81dbba785a4f65a236))

- **deps**: Update github-actions-updates to v7
  ([`8943aa9`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/8943aa9206775f4112bfc8d744da657c6bd0863d))

- **deps**: Update python-minor-patch
  ([`bfa291a`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/bfa291ab1631848d54dd6c691ca66ac94db725fb))

- **deps**: Update python-minor-patch
  ([`4b3e391`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/4b3e391e22374c738d90646c28b86c416aa20522))

### Code Style

- Fix Black formatting issues
  ([`6c32355`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/6c323552ae6286c82ecf9cf67509f7b5d50fa471))

- Fix black formatting on commited files
  ([`5f8a682`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/5f8a68205f5734696bd15e6b9d50797f49e5e07a))

- Fixed black lint issues on kb_postgres.py
  ([`d64674b`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/d64674b3d5c52b4155e45e974326e06789bfff6e))

- Lint issues fixed
  ([`4d1f720`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/4d1f7207318207172aef6bbb83d3ffae479643e8))

### Continuous Integration

- **workflows**: Install all extras to resolve missing PyJWT in tests
  ([`438359a`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/438359acd0c935f1f84cf383ba03a6b418cad67d))

### Features

- **chat-ui**: Lock input while waiting for assistant response
  ([`1f67aec`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/1f67aec22366ac71ceee863e69a69a88b73c2f64))

- **feedback**: Expose feedback_max_history_context as explicit plugin parameter
  ([`a5591de`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/a5591deba67b84b3d0a771c9858c1013de81233a))

- **feedback**: Store conversation history context window on feedback submission
  ([`3e49c2a`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/3e49c2a342110e5c3bd5bf3a86a07d3ade48270c))

### Refactoring

- DDLs for KB schemas moved into a separate .sql file
  ([`1dc68f0`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/1dc68f052681e91b476ac4ba58cbebb75a9752a5))

- **sso**: Make SSO dependencies optional via [sso] extras group
  ([`c685bd1`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/c685bd156d7ec8f82ed0f60ba31ce5df84fa65fb))


## v0.2.1 (2026-05-26)

### Bug Fixes

- **deps**: Update dependency express to v5
  ([`f84f121`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/f84f121b39f37ffd0a38692555ca97942d9f6dd7))

### Chores

- **deps**: Sync requirements.txt from poetry.lock
  ([`59551fc`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/59551fc4d6f6ecea3d21fb9f6a52f26cadd02990))

- **deps**: Sync requirements.txt from poetry.lock
  ([`aaed764`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/aaed76479961d3aefed0a19c23ff1c9ef98b894c))

- **deps**: Update dependency express to v4.22.2
  ([`ffdac83`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/ffdac83c8844bc21f2499edcf03118fd7dba358c))

- **deps**: Update dependency python to 3.14
  ([`5002d2e`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/5002d2e32eea4055adda6026f6972db62c51829d))

- **deps**: Update dependency swagger-jsdoc to v6.3.0
  ([`3da49b9`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/3da49b9f7e3cc3ff32346512d70a3ea46d35cd4f))

- **deps**: Update python-minor-patch
  ([`dbf8c8e`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/dbf8c8e1453a2334678f5fea15c3f4a3472bf686))

- **model**: Upgrade default bedrock model to claude 4.6 sonnet
  ([`76563ad`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/76563ad6ab959ba594b14bdf0de9dadd4661d1d3))


## v0.2.0 (2026-05-20)

### Chores

- **deps**: Sync requirements.txt from poetry.lock
  ([`02a9c63`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/02a9c63db81cd4704418482abb5b4ecd6a63f9f3))

### Features

- **config**: Add generic preset variable system (phase 1)
  ([`bb26f13`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/bb26f138cd77aeb4057f8f6be6633b5e37b26ef2))

- **sso**: Add federated IdP logout with id_token_hint support
  ([`a44a816`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/a44a816874e6373140166e1197eaa15e946e3d78))

### Refactoring

- Applied black litern changes to auto_bedrock_chat_fastapi/config.py
  ([`32fa579`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/32fa57985daad7e8dbe6ddcd1f49aff3fc0c0352))

- **chat-ui**: Replace variable modal with always-visible inputs
  ([`1f45573`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/1f45573aa6cb939fd81bd085eba168e27493700b))

- **preset-variables**: Remove UUID-specific validation and simplify variable inference
  ([`609b305`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/609b30532ad0d3b81cf7e25f8ce12a72235795ee))


## v0.1.3 (2026-05-08)

### Bug Fixes

- **cicd deps-sync**: Changed python version running on workflow to match Docker image python
  version (3.14 -> 3.11)
  ([`2e039b4`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/2e039b48bbf56b0820b3fa340d40655791cebbf5))


## v0.1.2 (2026-04-27)

### Bug Fixes

- **ci**: Normalize markers in deps-sync diff to handle poetry export variations
  ([`908b670`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/908b670918ae0d4ac4b5e0111731c0ef782a2900))

- **ci**: Normalize markers in deps-sync diff to handle poetry export variations
  ([`01f21da`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/01f21da7f56659c3dfed61322c2161f84bd6022e))

### Chores

- **deps**: Bump actions/cache from 3 to 5
  ([`b3578e4`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/b3578e4060e01398bbad8839ae307dd70bfda64b))

- **deps**: Bump actions/checkout from 4 to 6
  ([`71707d1`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/71707d1daf3b159e3974a1298fbf04c328a0a8c5))

- **deps**: Bump actions/setup-python from 5 to 6
  ([`d00691b`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/d00691be694a9222f869b02b56e2874aa60ac27f))

- **deps**: Bump actions/upload-artifact from 4 to 7
  ([`11b1bd5`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/11b1bd5df3d84e740a6de017c98dd72caacdbe3b))

- **deps**: Bump codecov/codecov-action from 3 to 6
  ([`effac26`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/effac26d9d156993ec1ce8208b36c1b4cc28e67c))

- **deps**: Bump docker/build-push-action from 4 to 7
  ([`02e3e85`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/02e3e85dec26087141cf1c84b1275819805a02d1))

- **deps**: Bump docker/setup-buildx-action from 2 to 4
  ([`bec9092`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/bec90924f71ba05ab1696ed021def7cf7a232a18))

- **deps**: Bump peaceiris/actions-gh-pages from 3 to 4
  ([`4e9cfa4`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/4e9cfa475c78a927cfa04196966bdfaf3709b2ac))

- **deps**: Changed workflow name to be more descriptive.
  ([`41e0c2e`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/41e0c2ea9fb48840aabdd43587172cd469cb5791))

- **deps**: Sync requirements.txt from poetry.lock
  ([`7c9a4f6`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/7c9a4f6999c54860d158164156404b8889da9ee6))

- **deps**: Sync requirements.txt from poetry.lock
  ([`2ee1cd0`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/2ee1cd0ce7e3aa0782d57c225828fe0379697c60))

- **deps**: Update dependency boto3 to v1.42.96
  ([`ffc2b94`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/ffc2b94ac955428a6d9d8092676a1787a7dbe467))

- **deps**: Update dependency highlight.js to v11.11.1
  ([`b396ac8`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/b396ac8fd551f14240a363f3d38301f17350ba42))

- **deps**: Update dependency nodemon to v3.1.14
  ([`30a6fd5`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/30a6fd51625119c41e02ffbb893a94410b64ea7a))

- **deps**: Update github-actions-updates
  ([`7a10b71`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/7a10b7112989be0031f03e8d70fd7b78c3043a0b))

- **deps**: Update github-actions-updates
  ([`b034cc6`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/b034cc6dca48f972341a47c3e25100baf60f26cb))

- **deps**: Update python-minor-patch
  ([`fbde5b0`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/fbde5b07a21f9c09263f83f9f64bd2cfdb325c96))

### Continuous Integration

- **deps**: Add deps-sync workflow to auto-regenerate requirements.txt
  ([`e66fa76`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/e66fa76cc1388d11a21f1bcbc903aac867a6b48e))

- **deps**: Replace Dependabot with Renovate for dependency management
  ([`a89d2b2`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/a89d2b2344b252da4afbbf66b56435228e11ded1))

- **deps**: Replace Dependabot with Renovate for dependency management
  ([`d515e1d`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/d515e1daae389605825d2dd2c1dd30fb4a7faefc))

- **deps-sync**: Merge poetry export into diff step to prevent empty file comparison
  ([`a35b336`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/a35b33636deff526af161f02ad340b204c42e242))

- **docs**: Added step for validating the existing of the lading page as well as linting for the
  markdown files.
  ([`f208989`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/f2089899c4163673b0f16d95f6a2ed0113e20187))

- **docs**: Replace broken Sphinx workflow with wiki sync pipeline
  ([`966a620`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/966a62007db5b990557362f7108cd6b5a1a085ae))

- **docs**: Replaced 'git push' with 'git push origin HEAD:main' to ensure the push goes into the
  main branch.
  ([`538f437`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/538f43720db4c70abd00aa5a39a134211e831f2b))


## v0.1.1 (2026-04-21)

### Chores

- **readme**: Removed broken emojis from README.
  ([`19cb3af`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/19cb3af968ce2a501b9a3a81e4777d72fb94d986))

- **readme**: Update required python version specified on readme file.
  ([`6f38ddd`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/6f38ddd2e3dd0f7a1d2e9c22d67b681ffc3c168a))

- **readme**: Updated required python version reference on python badge at the top of the readme
  file.
  ([`f7c82da`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/f7c82dad9cf3a4cafd0f2146a234e9d823db0d15))

### Continuous Integration

- **deps**: Enable Dependabot and automated dependency sync checks
  ([`8084247`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/808424768d23c452d460b1b06bb5e36e6b88e170))

- **deps**: Fix CI compatibility for Python 3.10+ and coverage badge
  ([`df3b351`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/df3b351b38a0b5715c91b813dd28475ca2acca6f))

- **deps**: Pin setuptools<81 and align requirements.txt markers
  ([`fe185d8`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/fe185d8ad5a1477cdf6770b56d90da31c187acd7))

### Features

- **rag**: Phase 1 complete - production-ready RAG system
  ([`b422bb8`](https://github.com/gabrielbriones/auto-bedrock-chat-fastapi/commit/b422bb8b2ee2e9a6d02a2f63c3565e4884063d17))


## v0.1.0 (2025-11-12)

- Initial Release
