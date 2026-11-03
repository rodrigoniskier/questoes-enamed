from datetime import datetime
from datetime import timezone as datetime_timezone

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import ComponenteCurricular, Periodo, Questao, Semestre


@override_settings(TIME_ZONE="America/Fortaleza")
class SubmissionDateTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        semester = Semestre.objects.create(nome="2026.2")
        period = Periodo.objects.create(nome="2º Período", semestre=semester)
        component = ComponenteCurricular.objects.create(nome="Clínica", periodo=period)
        cls.question = Questao.objects.create(
            componente=component,
            professor_nome="Docente sintético",
            professor_email="docente@example.com",
            enunciado="Questão sintética",
            justificativa="Justificativa sintética",
        )
        cls.reviewer = get_user_model().objects.create_superuser(
            "revisor-datas", "revisor@example.com", "teste"
        )

    def test_new_question_is_timestamped_and_edits_preserve_original_date(self):
        question = Questao.objects.create(
            componente=self.question.componente, enunciado="Nova questão", justificativa="Justificativa"
        )
        submitted = question.enviada_em
        self.assertTrue(timezone.is_aware(submitted))
        self.assertLess(abs((timezone.now() - submitted).total_seconds()), 10)
        question.enunciado = "Enunciado corrigido"
        question.status = "APROVADA"
        question.save()
        question.refresh_from_db()
        self.assertEqual(question.enviada_em, submitted)

    def test_date_is_visible_in_local_timezone_on_admin_and_pre_audit(self):
        # 01:05 UTC do dia 04 ainda é dia 03 no fuso local.
        submitted = datetime(2026, 11, 4, 1, 5, tzinfo=datetime_timezone.utc)
        Questao.objects.filter(pk=self.question.pk).update(enviada_em=submitted)
        self.client.force_login(self.reviewer)
        for route, args in (
            ("admin:questoes_questao_changelist", []),
            ("admin:questoes_questao_change", [self.question.pk]),
            ("pre_auditoria:lista", []),
            ("pre_auditoria:detalhe", [self.question.pk]),
        ):
            with self.subTest(route=route):
                response = self.client.get(reverse(route, args=args))
                self.assertContains(response, "Data de envio")
                self.assertContains(response, "03/11/2026 22:05")
                if route == "admin:questoes_questao_change":
                    self.assertNotIn("enviada_em", response.context["adminform"].form.fields)

    def test_legacy_unknown_date_is_explicit_and_not_filled_on_edit(self):
        Questao.objects.filter(pk=self.question.pk).update(enviada_em=None)
        self.question.refresh_from_db()
        self.question.enunciado = "Correção do texto antigo"
        self.question.save()
        self.question.refresh_from_db()
        self.assertIsNone(self.question.enviada_em)
        self.client.force_login(self.reviewer)
        for route, args in (
            ("admin:questoes_questao_changelist", []),
            ("admin:questoes_questao_change", [self.question.pk]),
            ("pre_auditoria:lista", []),
            ("pre_auditoria:detalhe", [self.question.pk]),
        ):
            with self.subTest(route=route):
                self.assertContains(self.client.get(reverse(route, args=args)), "Não registrada")

    def test_admin_can_sort_and_filter_by_submission_date(self):
        self.client.force_login(self.reviewer)
        url = reverse("admin:questoes_questao_changelist")
        old = datetime(2026, 10, 1, 12, tzinfo=datetime_timezone.utc)
        Questao.objects.filter(pk=self.question.pk).update(enviada_em=old)
        newer = Questao.objects.create(
            componente=self.question.componente, enunciado="Mais recente", justificativa="J"
        )
        response = self.client.get(url)
        column = response.context["cl"].list_display.index("data_envio")
        for ordering, expected in (
            (str(column), [self.question.pk, newer.pk]),
            (f"-{column}", [newer.pk, self.question.pk]),
        ):
            response = self.client.get(url, {"o": ordering})
            self.assertEqual([q.pk for q in response.context["cl"].result_list], expected)
        response = self.client.get(
            url,
            {"enviada_em__gte": "2026-10-01T00:00:00-03:00", "enviada_em__lt": "2026-10-02T00:00:00-03:00"},
        )
        self.assertEqual([q.pk for q in response.context["cl"].result_list], [self.question.pk])
