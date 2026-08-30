## 이 저장소

- **프로젝트가 무엇인지: 루트 [README.md](README.md)를 먼저 읽는다.** 데이터 흐름, 하루에 무엇이 도는지, 스택이 거기 있다.
- 저장소를 돌리는 법(설정·DB alias·마이그레이션·배포·관측): [docs/operations.md](docs/operations.md)
- 작업 규칙: [.claude/CLAUDE.md](.claude/CLAUDE.md)
- 코드를 고쳐 README의 숫자·목록이 낡으면 **같은 커밋에서** README도 고친다.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
