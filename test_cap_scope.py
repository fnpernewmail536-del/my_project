"""Upper-cap validation must only inspect the requested level targets."""
from types import SimpleNamespace as NS
import unittest
from local_chat.conditional_engine import compile_conditional
from local_chat.parser import ParseError


class CapScopeTests(unittest.TestCase):
    def setUp(self):
        self.metadata = {'characters': [
            {'id':1,'rarity':0,'names':['fixture-basic'],'collab':False},
            {'id':2,'rarity':2,'names':['fixture-rare'],'collab':False},
            {'id':878,'rarity':4,'names':['fixture-new-uber'],'collab':False},
        ], 'groups':[]}
        rows=[]
        for rarity in (0,2):
            row=[NS(to_int=lambda:0) for _ in range(52)]
            row[13]=NS(to_int=lambda rarity=rarity:rarity)
            row[50]=NS(to_int=lambda:50)
            row[51]=NS(to_int=lambda:90)
            rows.append(row)
        self.rows=rows
        self.module=NS(get_character_metadata=lambda:self.metadata,
                       _latest_jp_game_data_getter=lambda:NS(download=lambda *args:self.rows),
                       core=NS(CSV=lambda value:value))

    def compile(self, selector, field='plus'):
        return compile_conditional(self.module, {'conditional_rules':[
            {'domain':'cat','field':field,'expr':{'op':'set','value':1},'selector':selector}
        ]})

    def test_unrelated_new_uber_does_not_block_rare_plus(self):
        compiled=self.compile({'rarity':2})
        self.assertEqual(compiled['rules'][0]['ids'],[2])
        self.assertEqual(compiled['caps'],{2:{'base':50,'plus':90}})

    def test_requested_new_uber_still_blocks_missing_caps(self):
        with self.assertRaisesRegex(ParseError,'No.878'):
            self.compile({'rarity':4})

    def test_selected_rarity_mismatch_is_still_rejected(self):
        self.rows[1][13]=NS(to_int=lambda:4)
        with self.assertRaisesRegex(ParseError,'No.2'):
            self.compile({'rarity':2})

    def test_unlock_only_does_not_need_level_caps(self):
        compiled=self.compile({'rarity':4},field='unlocked')
        self.assertEqual(compiled['rules'][0]['ids'],[878])
        self.assertNotIn('caps',compiled)


if __name__=='__main__':unittest.main()
