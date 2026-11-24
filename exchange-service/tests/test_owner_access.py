import unittest
from owner_access import (
    OwnerAuthorizationError, require_owner_permission, resolve_owner_role
)

TOKENS={"owner":"owner-secret-012345678901234567890123","finance":"finance-secret-01234567890123456789",
        "compliance":"compliance-secret-0123456789012345678","legacy_admin":""}

class OwnerAccessTests(unittest.TestCase):
    def test_roles_are_resolved(self):
        self.assertEqual(resolve_owner_role("Bearer "+TOKENS["owner"],TOKENS),"owner")
        self.assertEqual(resolve_owner_role("Bearer "+TOKENS["finance"],TOKENS),"finance")
        self.assertEqual(resolve_owner_role("Bearer "+TOKENS["compliance"],TOKENS),"compliance")
    def test_finance_cannot_read_customer_order_list(self):
        with self.assertRaises(OwnerAuthorizationError):
            require_owner_permission("Bearer "+TOKENS["finance"],"orders",TOKENS)
    def test_compliance_can_read_order_and_reconciliation(self):
        self.assertEqual(require_owner_permission("Bearer "+TOKENS["compliance"],"orders",TOKENS),"compliance")
        self.assertEqual(require_owner_permission("Bearer "+TOKENS["compliance"],"reconciliation",TOKENS),"compliance")
    def test_legacy_admin_maps_to_owner(self):
        tokens={**TOKENS,"legacy_admin":"legacy-admin-secret"}
        self.assertEqual(require_owner_permission("Bearer legacy-admin-secret","orders",tokens),"owner")
    def test_unknown_token_is_rejected(self):
        with self.assertRaises(OwnerAuthorizationError):
            resolve_owner_role("Bearer invalid",TOKENS)
    def test_missing_configuration_fails_closed(self):
        with self.assertRaises(RuntimeError):
            resolve_owner_role("Bearer any",{})
    def test_empty_bearer_is_rejected(self):
        with self.assertRaises(OwnerAuthorizationError):
            resolve_owner_role(None,TOKENS)

if __name__=="__main__": unittest.main()
