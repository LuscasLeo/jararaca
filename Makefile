bump-type ?= patch

fast-release:
	poetry version $(bump-type) && \
	git add pyproject.toml && \
	git commit -m $$(poetry version -s) && \
	git tag $$(poetry version -s) && \
	git push origin main --tags && \
	poetry publish --build

release:
	poetry version $(bump-type) && \
	git add pyproject.toml && \
	git commit -m $$(poetry version -s) && \
	git tag $$(poetry version -s) && \
	git push origin main --tags && \
	gh release create $$(poetry version -s) --title $$(poetry version -s) --generate-notes
