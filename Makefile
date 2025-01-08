

fast-release:
	poetry version patch && \
	git add pyproject.toml && \
	git commit -m $$(poetry version -s) && \
	git tag $$(poetry version -s) && \
	git push origin main --tags && \
	poetry publish --build
