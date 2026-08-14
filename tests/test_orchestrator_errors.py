import unittest

from orchestrator.core import errors as E


class TestErrorTaxonomy(unittest.TestCase):
    def test_payload_shape_matches_ca_contract(self):
        err = E.UnsupportedField("Unsupported field(s) for 'x': foo",
                                 unsupported_fields=["foo"])
        payload = err.to_payload()
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error_name"], "UNSUPPORTED_FIELD")
        self.assertEqual(payload["ca_next_action"], "remove_unsupported_field")
        self.assertEqual(payload["unsupported_fields"], ["foo"])
        self.assertEqual(err.status, 400)
        self.assertTrue(err.recoverable)

    def test_hierarchy_lets_handlers_catch_by_domain(self):
        self.assertTrue(issubclass(E.UnsupportedField, E.SchemaViolation))
        self.assertTrue(issubclass(E.SchemaViolation, E.ContractError))
        self.assertTrue(issubclass(E.MutationTokenRequired, E.AuthError))
        self.assertTrue(issubclass(E.CouncilModeNotSupported, E.UserInputError))
        self.assertTrue(issubclass(E.WorkerTimeout, E.ExternalRuntimeError))
        for cls in (E.ContractError, E.AuthError, E.PermissionPolicyError,
                    E.ExternalRuntimeError, E.InternalBugError):
            self.assertTrue(issubclass(cls, E.OrchestratorError))

    def test_status_codes_follow_http_semantics(self):
        self.assertEqual(E.UserInputError().status, 400)
        self.assertEqual(E.AuthError().status, 401)
        self.assertEqual(E.PermissionPolicyError().status, 403)
        self.assertEqual(E.NotFoundError().status, 404)
        self.assertEqual(E.ConflictError().status, 409)
        self.assertEqual(E.ExternalRuntimeError().status, 502)
        self.assertEqual(E.InternalBugError().status, 500)

    def test_error_names_unique_across_taxonomy(self):
        names = {}
        for attr in dir(E):
            cls = getattr(E, attr)
            if isinstance(cls, type) and issubclass(cls, E.OrchestratorError):
                # Intermediate bases may share names with no concrete duplicates;
                # assert concrete leaf names used by the CA contract are unique.
                names.setdefault(cls.error_name, set()).add(cls.__name__)
        for name, classes in names.items():
            # A name may only be shared along one inheritance chain.
            if len(classes) > 1:
                chain_ok = all(
                    issubclass(getattr(E, a), getattr(E, b)) or issubclass(getattr(E, b), getattr(E, a))
                    for a in classes for b in classes
                )
                self.assertTrue(chain_ok, f"error_name '{name}' reused across unrelated classes: {classes}")


if __name__ == "__main__":
    unittest.main()
