import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from rustdesk_mcp.framing import Parser, wrapper
from rustdesk_mcp.operations import script


@unittest.skipUnless(os.name=='nt' and shutil.which('powershell'),'Windows PowerShell required')
class CatalogTest(unittest.TestCase):
    def run_script(self,resource,params):
        payload=wrapper(script(resource,params),'catalog').rstrip('\r')+'\n'
        process=subprocess.run(['powershell','-NoLogo','-NoProfile','-NonInteractive','-Command','-'],
            input=payload.encode('ascii'),stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=45,
            creationflags=subprocess.CREATE_NO_WINDOW)
        output,meta=Parser('catalog').feed(process.stdout)
        self.assertIsNotNone(meta,process.stdout.decode('utf-8',errors='replace')[-4000:])
        self.assertTrue(meta['powershell_success'],output)
        return json.loads(output.strip())

    def test_file_encoding_hash_conflict_edit_and_safe_delete(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'中文.txt'
            original=b'\xef\xbb\xbf'+ '中文😀\r\nunique\r\n'.encode()
            path.write_bytes(original)
            args={'remote_path':str(path),'roots':[root],'operation_tag':'regression','encoding':'auto',
                  'offset':0,'max_chars':4,'start_line':1,'tail_lines':0}
            read=self.run_script('files.ps1',{**args,'kind':'read_file'})
            self.assertEqual(read['data']['text'],'中文😀\r')
            self.assertEqual(read['data']['sha256'],hashlib.sha256(original).hexdigest())
            edit={**args,'kind':'edit_file','old_text':'unique','new_text':'changed','replace_all':False,'expected_sha256':'0'*64}
            conflict=self.run_script('files.ps1',edit)
            self.assertEqual(conflict['error']['code'],'file_conflict')
            self.assertEqual(path.read_bytes(),original)
            changed=self.run_script('files.ps1',{**edit,'expected_sha256':read['data']['sha256']})
            self.assertTrue(changed['ok'],changed)
            self.assertEqual(path.read_bytes(),b'\xef\xbb\xbf'+'中文😀\r\nchanged\r\n'.encode())
            self.assertEqual(Path(changed['data']['backup_path']).read_bytes(),original)
            removed=self.run_script('files.ps1',{**args,'kind':'remove_file','expected_sha256':changed['data']['sha256']})
            self.assertTrue(removed['ok'],removed)
            self.assertFalse(path.exists())

    def test_diagnostic_pagination_and_protected_targets(self):
        result=self.run_script('diagnostics.ps1',{'kind':'list_processes','offset':0,'limit':2})
        self.assertTrue(result['ok'],result)
        self.assertEqual(len(result['data']['items']),2)
        self.assertIn('creation_time',result['data']['items'][0])
        denied=self.run_script('diagnostics.ps1',{'kind':'kill_process','pid':4})
        self.assertEqual(denied['error']['code'],'protected_process')
        denied=self.run_script('diagnostics.ps1',{'kind':'control_service','name':'RpcSs','action':'stop'})
        self.assertEqual(denied['error']['code'],'protected_service')
