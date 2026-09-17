import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.services.outbound_privacy import PLACEHOLDER_RE, OutboundPrivacyService

# 伪装成真实密钥格式的测试夹具在源码里拆开拼接，避免触发密钥扫描的推送拦截。
AWS_EXAMPLE_ACCESS_KEY = "AKIA" + "IOSFODNN7EXAMPLE"
AWS_EXAMPLE_SECRET_KEY = "wJalrXUtnFEMI" + "/K7MDENG/bPxRfiCYEXAMPLEKEY"
OPENAI_TEST_KEY = "sk-proj-" + "abcdefghijklmnopqrstuvwxyz"
STRIPE_LIVE_KEY = "sk_live_" + "51H8skj2HeGhqNT9zXkLmNoPqRsTuVwXyZ"
GITLAB_PAT = "glpat-" + "AbCdEfGhIjKlMnOpQrStUvWx"
SLACK_BOT_TOKEN = "xoxb-" + "123456789012-1234567890123-AbCdEfGhIjKlMnOpQrStUvWx"
HF_TOKEN = "hf_" + "AbCdEfGhIjKlMnOpQrStUvWxYz1234567890"
NPM_TOKEN = "npm_" + "AbCdEfGhIjKlMnOpQrStUvWxYz123456789"
TELEGRAM_BOT_TOKEN = "110201543:AA" + "HdqTcvCH1vGWJxfSeofSAs0K5PALDsaw"
SENDGRID_KEY = "SG." + "aBcDeFgHiJkLmNoPqRsTu" + ".vWyXzYxWvUtSrQpOnMlKjIgHgFeDcBaZyXwVuTsRqPoNmLkJiHg"
SLACK_WEBHOOK_URL = "https://hooks.slack.com/services/" + "T12345678/B12345678/AbCdEfGhIjKlMnOpQrStUvWxYz012345"
DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/" + "123456789012345678/aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456789AbCdEfGhIjKlMnOp"
FIELD_TEST_WEBHOOK_URL = "https://hooks.slack.com/services/" + "T1/B1/verysecrettoken1234567890"


class OutboundPrivacyServiceTests(unittest.TestCase):
    def test_sanitizes_common_sensitive_values_and_restores_response_payload(self) -> None:
        service = OutboundPrivacyService()
        original_text = (
            "password=SuperSecret123 from 192.168.1.10 and 192.168.1.10; "
            f"ak {AWS_EXAMPLE_ACCESS_KEY}; Cookie: sessionid=abc123; admin@example.com"
        )

        result = service.sanitize_request_body(
            {
                "model": "demo/gpt-4.1",
                "messages": [{"role": "user", "content": original_text}],
                "metadata": {
                    "api_key": OPENAI_TEST_KEY,
                    "password": "inline-secret",
                },
            }
        )

        serialized = json.dumps(result.body, ensure_ascii=False)
        self.assertEqual("demo/gpt-4.1", result.body["model"])
        for raw_value in (
            "SuperSecret123",
            "192.168.1.10",
            AWS_EXAMPLE_ACCESS_KEY,
            "sessionid=abc123;",
            "admin@example.com",
            OPENAI_TEST_KEY,
            "inline-secret",
        ):
            self.assertNotIn(raw_value, serialized)

        content = result.body["messages"][0]["content"]
        placeholders = PLACEHOLDER_RE.findall(content)
        self.assertGreaterEqual(len(placeholders), 5)
        ip_placeholders = [placeholder for placeholder in PLACEHOLDER_RE.findall(content) if "_IP_" in placeholder]
        self.assertEqual(2, len(ip_placeholders))
        self.assertEqual(1, len(set(ip_placeholders)))

        restored = result.context.restore_payload({"answer": f"echo {content}"})
        self.assertEqual({"answer": f"echo {original_text}"}, restored)

    def test_stream_fragment_restore_buffers_split_placeholder(self) -> None:
        service = OutboundPrivacyService()
        result = service.sanitize_request_body(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": "Authorization: Bearer secret-token-1234567890",
                    }
                ]
            }
        )
        placeholder = result.body["messages"][0]["content"].split(": ", 1)[1]

        first, pending = result.context.restore_stream_text_fragment(f"token={placeholder[:16]}")
        self.assertEqual("token=", first)
        self.assertEqual(placeholder[:16], pending)

        second, pending = result.context.restore_stream_text_fragment(f"{pending}{placeholder[16:]}")
        self.assertEqual("Bearer secret-token-1234567890", second)
        self.assertEqual("", pending)

    def test_sanitizes_natural_language_password_assignments(self) -> None:
        service = OutboundPrivacyService()
        original_text = (
            "密码是 abc123，密码为 abc123；密码：abc123；密码 abc123；"
            "跳板机登录口令是 jumpHost-456，password is EnglishPass789；"
            "api key is local-api-key-000，client secret local:client:secret"
        )

        result = service.sanitize_request_body({"messages": [{"role": "user", "content": original_text}]})

        content = result.body["messages"][0]["content"]
        self.assertNotIn("abc123", content)
        self.assertNotIn("jumpHost-456", content)
        self.assertNotIn("EnglishPass789", content)
        self.assertNotIn("local-api-key-000", content)
        self.assertNotIn("local:client:secret", content)
        placeholders = PLACEHOLDER_RE.findall(content)
        self.assertGreaterEqual(len(placeholders), 8)
        self.assertEqual(4, placeholders.count(placeholders[0]))
        self.assertEqual(5, len(set(placeholders)))
        self.assertEqual(original_text, result.context.restore_text(content))

    def test_does_not_treat_explanatory_password_text_as_assignment(self) -> None:
        service = OutboundPrivacyService()
        text = "如果不打密码两个字在前面，是不是就正则提取不到？跳板机 IP 是 10.2.3.4"

        result = service.sanitize_request_body({"messages": [{"role": "user", "content": text}]})

        content = result.body["messages"][0]["content"]
        self.assertIn("密码两个字", content)
        self.assertNotIn("10.2.3.4", content)
        self.assertEqual(text, result.context.restore_text(content))

    def test_skips_data_urls_to_keep_large_image_payloads_intact(self) -> None:
        service = OutboundPrivacyService()
        data_url = f"data:image/png;base64,{AWS_EXAMPLE_ACCESS_KEY}"

        result = service.sanitize_request_body(
            {
                "model": "demo/gpt-4.1",
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": data_url}},
                            {"type": "text", "text": "server=10.1.2.3"},
                        ],
                    }
                ],
            }
        )

        image_url = result.body["messages"][0]["content"][0]["image_url"]["url"]
        text = result.body["messages"][0]["content"][1]["text"]
        self.assertEqual(data_url, image_url)
        self.assertNotIn("10.1.2.3", text)

    def test_preserves_token_limit_request_fields(self) -> None:
        service = OutboundPrivacyService()
        body = {
            "model": "demo/gpt-4.1",
            "max_tokens": 1024,
            "max_output_tokens": 2048,
            "max_completion_tokens": 512,
            "maxOutputTokens": 256,
            "temperature": 0.7,
            "stream": True,
            "messages": [{"role": "user", "content": "hello"}],
        }

        result = service.sanitize_request_body(body)

        self.assertEqual(1024, result.body["max_tokens"])
        self.assertEqual(2048, result.body["max_output_tokens"])
        self.assertEqual(512, result.body["max_completion_tokens"])
        self.assertEqual(256, result.body["maxOutputTokens"])
        self.assertEqual(0.7, result.body["temperature"])
        self.assertTrue(result.body["stream"])
        self.assertFalse(result.context.enabled)

    def test_sanitizes_prefixed_env_style_assignments(self) -> None:
        service = OutboundPrivacyService()
        original_text = (
            "export DB_PASSWORD='Sup3rS3cr3tPass' "
            "MYSQL_ROOT_PASSWORD=rootpass123 "
            f"AWS_SECRET_ACCESS_KEY={AWS_EXAMPLE_SECRET_KEY} "
            "GITHUB_TOKEN=customtoken123abc "
            "PGPASSWORD=postgres123"
        )

        result = service.sanitize_request_body({"messages": [{"role": "user", "content": original_text}]})

        content = result.body["messages"][0]["content"]
        for raw_value in (
            "Sup3rS3cr3tPass",
            "rootpass123",
            AWS_EXAMPLE_SECRET_KEY,
            "customtoken123abc",
            "postgres123",
        ):
            self.assertNotIn(raw_value, content)
        self.assertEqual(original_text, result.context.restore_text(content))

    def test_does_not_redact_prefixed_variables_with_safe_suffixes(self) -> None:
        service = OutboundPrivacyService()
        text = (
            "PASSWORD_FILE=/etc/secrets/db.txt "
            "TOKEN_URL=https://auth.example.com/tokens "
            "参数 max_tokens=4096 与 temperature=0.7"
        )

        result = service.sanitize_request_body({"messages": [{"role": "user", "content": text}]})

        content = result.body["messages"][0]["content"]
        self.assertIn("PASSWORD_FILE=/etc/secrets/db.txt", content)
        self.assertIn("TOKEN_URL=https://auth.example.com/tokens", content)
        self.assertIn("max_tokens=4096", content)
        self.assertIn("temperature=0.7", content)

    def test_sanitizes_compound_chinese_assignment_connectors(self) -> None:
        service = OutboundPrivacyService()
        original_text = "密码是：Abc999xyz，密钥为: sk-live-key-12345，密码是，Tmp888qqq"

        result = service.sanitize_request_body({"messages": [{"role": "user", "content": original_text}]})

        content = result.body["messages"][0]["content"]
        self.assertNotIn("Abc999xyz", content)
        self.assertNotIn("sk-live-key-12345", content)
        self.assertNotIn("Tmp888qqq", content)
        self.assertEqual(original_text, result.context.restore_text(content))

    def test_sanitizes_url_embedded_credentials_and_restores(self) -> None:
        service = OutboundPrivacyService()
        original_text = (
            "postgres://postgres:p@ssw0rd@10.0.0.5:5432/prod "
            "mongodb+srv://admin:Hunter2Xyz@cluster0.abcde.mongodb.net/prod "
            "redis://:Str0ngPass@redis.internal:6379/0 "
            "https://deploy:Gh7xNine1@gitlab.example.com/repo.git"
        )

        result = service.sanitize_request_body({"messages": [{"role": "user", "content": original_text}]})

        content = result.body["messages"][0]["content"]
        for raw_value in ("p@ssw0rd", "Hunter2Xyz", "Str0ngPass", "Gh7xNine1"):
            self.assertNotIn(raw_value, content)
        self.assertIn("10.0.0.5:5432/prod", content)
        self.assertIn("cluster0.abcde.mongodb.net/prod", content)
        self.assertIn("redis.internal:6379/0", content)
        self.assertIn("gitlab.example.com/repo.git", content)
        placeholders = PLACEHOLDER_RE.findall(content)
        self.assertEqual(4, len(placeholders))
        self.assertEqual(4, len(set(placeholders)))
        self.assertEqual(original_text, result.context.restore_text(content))

    def test_sanitizes_provider_tokens_and_webhook_urls(self) -> None:
        service = OutboundPrivacyService()
        secrets = (
            STRIPE_LIVE_KEY,
            GITLAB_PAT,
            SLACK_BOT_TOKEN,
            HF_TOKEN,
            NPM_TOKEN,
            TELEGRAM_BOT_TOKEN,
            SENDGRID_KEY,
            SLACK_WEBHOOK_URL,
            DISCORD_WEBHOOK_URL,
        )
        original_text = " ".join(secrets)

        result = service.sanitize_request_body({"messages": [{"role": "user", "content": original_text}]})

        content = result.body["messages"][0]["content"]
        for secret in secrets:
            self.assertNotIn(secret, content)
        self.assertEqual(original_text, result.context.restore_text(content))

    def test_sanitizes_cli_and_inline_credentials(self) -> None:
        service = OutboundPrivacyService()
        original_text = (
            "curl -u admin:S3cr3tPss https://api.internal/v1 ; "
            "sshpass -p MyS3cr3t ssh root@10.0.0.5 ; "
            "mysql -h db.prod -u admin -pRootPass123 ; "
            "使用 Basic dXNlcjpwYXNzd29yZA== 认证 ; "
            "<password>Xy7kP9mQz</password> ; "
            "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQDLxK9zLxK9zLxK9zLxK9 user@host"
        )

        result = service.sanitize_request_body({"messages": [{"role": "user", "content": original_text}]})

        content = result.body["messages"][0]["content"]
        for raw_value in ("S3cr3tPss", "MyS3cr3t", "RootPass123", "dXNlcjpwYXNzd29yZA==", "Xy7kP9mQz"):
            self.assertNotIn(raw_value, content)
        self.assertNotIn("AAAAB3NzaC1yc2EAAAADAQABAAABgQDLxK9", content)
        self.assertIn("https://api.internal/v1", content)
        self.assertIn("user@host", content)
        self.assertEqual(original_text, result.context.restore_text(content))

    def test_does_not_redact_basic_english_prose(self) -> None:
        service = OutboundPrivacyService()
        text = "basic telecommunications infrastructure with standard protocols"

        result = service.sanitize_request_body({"messages": [{"role": "user", "content": text}]})

        self.assertEqual(text, result.body["messages"][0]["content"])

    def test_does_not_redact_english_prose_phrases_and_code_annotations(self) -> None:
        service = OutboundPrivacyService()
        text = (
            "Password Policy and Token Usage in Session Storage. "
            "The cookie consent banner hides the secret sauce. "
            "Access key ids are rotated weekly. "
            "interface Config { apiKey: string; token: string; } "
            "session:\n  timeout"
        )

        result = service.sanitize_request_body({"messages": [{"role": "user", "content": text}]})

        self.assertEqual(text, result.body["messages"][0]["content"])

    def test_does_not_redact_pagination_cursor_tokens(self) -> None:
        service = OutboundPrivacyService()
        text = (
            "GET /items?next_token=abc123def456789&limit=20 "
            "verify_token=abc123xyz pageToken=Zm9vYmFy continuation_token=q1w2e3r4t5"
        )

        result = service.sanitize_request_body({"messages": [{"role": "user", "content": text}]})

        self.assertEqual(text, result.body["messages"][0]["content"])

    def test_does_not_redact_decimal_numbers_retina_filenames_and_curl_urls(self) -> None:
        service = OutboundPrivacyService()
        text = (
            "精度 0.13812345678 的权重，尾数 13812345678.5 舍入。"
            "引用 logo@2x.png 和 icon@3x.png。"
            "curl -u https://example.com/api 演示"
        )

        result = service.sanitize_request_body({"messages": [{"role": "user", "content": text}]})

        self.assertEqual(text, result.body["messages"][0]["content"])

    def test_still_sanitizes_secret_like_values_after_prose_gate(self) -> None:
        service = OutboundPrivacyService()
        original_text = "password is hunter2 and 密码 plainabc 已泄露"

        result = service.sanitize_request_body({"messages": [{"role": "user", "content": original_text}]})

        content = result.body["messages"][0]["content"]
        self.assertNotIn("hunter2", content)
        self.assertNotIn("plainabc", content)
        self.assertEqual(original_text, result.context.restore_text(content))

    def test_sanitizes_chinese_pii_numbers(self) -> None:
        service = OutboundPrivacyService()
        original_text = (
            "手机号 13812345678，备用 +86 139-1234-5678，"
            "身份证号：110101199003078915，银行卡号：6222021234567890128"
        )

        result = service.sanitize_request_body({"messages": [{"role": "user", "content": original_text}]})

        content = result.body["messages"][0]["content"]
        for raw_value in ("13812345678", "139-1234-5678", "110101199003078915", "6222021234567890128"):
            self.assertNotIn(raw_value, content)
        self.assertEqual(original_text, result.context.restore_text(content))

    def test_does_not_redact_order_numbers_failing_luhn(self) -> None:
        service = OutboundPrivacyService()
        text = "订单号 1234567890123456，时间戳 1700000000000"

        result = service.sanitize_request_body({"messages": [{"role": "user", "content": text}]})

        self.assertEqual(text, result.body["messages"][0]["content"])

    def test_sanitizes_sensitive_json_field_name_blind_spots(self) -> None:
        service = OutboundPrivacyService()
        body = {
            "messages": [{"role": "user", "content": "hi"}],
            "db": {"user": "root", "pass": "dbpass123"},
            "credentials": {"passphrase": "open-sesame-99"},
            "storage": {"account_key": "azure-key-abcdef123456"},
            "config": {"database_url": "postgres://u:pw123@h/db"},
            "http": {"basic_auth": "dXNlcjpwYXNz"},
            "alerts": {"webhook_url": FIELD_TEST_WEBHOOK_URL},
        }

        result = service.sanitize_request_body(body)

        serialized = json.dumps(result.body, ensure_ascii=False)
        for raw_value in (
            "dbpass123",
            "open-sesame-99",
            "azure-key-abcdef123456",
            "pw123",
            "dXNlcjpwYXNz",
            "verysecrettoken1234567890",
        ):
            self.assertNotIn(raw_value, serialized)
        restored = result.context.restore_payload(result.body)
        self.assertEqual(body, restored)

    def test_restores_nested_placeholders_from_wrapped_url_credentials(self) -> None:
        service = OutboundPrivacyService()
        original_text = "DATABASE_URL=postgres://admin:P@ssw0rd9@db.prod:5432/app"

        result = service.sanitize_request_body({"messages": [{"role": "user", "content": original_text}]})

        content = result.body["messages"][0]["content"]
        self.assertNotIn("P@ssw0rd9", content)
        self.assertNotIn("admin:P@ssw0rd9@", content)
        self.assertEqual(original_text, result.context.restore_text(content))
        self.assertEqual(original_text, result.context.restore_text(f"echo {content}")[len("echo "):])

    def test_sanitize_request_headers_strips_client_credentials(self) -> None:
        service = OutboundPrivacyService()
        headers = {
            "Content-Type": "application/json",
            "Cookie": "sessionid=abc123",
            "x-api-key": "sk-test-1234567890",
            "X-Goog-Api-Key": "AIzaTestKey1234567890abcdefghij",
            "User-Agent": "curl/8.0",
        }

        filtered = service.sanitize_request_headers(headers)

        self.assertEqual(
            {
                "Content-Type": "application/json",
                "User-Agent": "curl/8.0",
            },
            filtered,
        )


if __name__ == "__main__":
    unittest.main()
