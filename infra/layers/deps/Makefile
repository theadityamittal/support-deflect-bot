build-DepsLayer:
	python3 -m pip install -r requirements.txt -t "$(ARTIFACTS_DIR)/python" \
		--platform manylinux2014_aarch64 \
		--implementation cp \
		--python-version 3.12 \
		--only-binary=:all:
	rm -rf "$(ARTIFACTS_DIR)/python/slack"
