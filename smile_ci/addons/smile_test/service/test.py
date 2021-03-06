# -*- coding: utf-8 -*-
##############################################################################
#
#    OpenERP, Open Source Management Solution
#    Copyright (C) 2014 Smile (<http://www.smile.fr>).
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU Affero General Public License as
#    published by the Free Software Foundation, either version 3 of the
#    License, or (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU Affero General Public License for more details.
#
#    You should have received a copy of the GNU Affero General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
##############################################################################

import csv
from contextlib import closing
import inspect
import os
import time
import traceback

try:
    # For Odoo >= 6.1
    from openerp import release, sql_db, tools
    from openerp.modules.module import load_information_from_description_file
except ImportError:
    try:
        # For Odoo 5.0 and 6.0
        from addons import load_information_from_description_file
        import release
        import sql_db
        import tools
    except ImportError:
        raise ImportError("Odoo version not supported")


try:
    # For Odoo >= 8.0
    from openerp.service import common, security
except ImportError:
    try:
        # For Odoo 6.1 and 7.0
        from openerp.service.web_services import common
        from openerp.service import security
    except ImportError:
        try:
            # For Odoo 5.0 and 6.0
            from service.web_services import common
            from service import security
        except ImportError:
            raise ImportError("Odoo version not supported")

try:
    # For Odoo >= 7.0
    from openerp.modules.module import run_unit_tests
except ImportError:
    run_unit_tests = None


KEYS = ['module', 'result', 'code', 'file', 'line', 'exception', 'duration']


def _write_log(vals):
    filename = tools.config.get('test_logfile')
    if not filename:
        return
    if not os.path.exists(filename):
        with open(filename, 'wb') as f:
            writer = csv.writer(f)
            writer.writerow(KEYS)
    with open(filename, 'ab') as f:
        writer = csv.writer(f)
        writer.writerow([vals.get(key, '') for key in KEYS])


def _get_modules_list(cr):
    cr.execute("SELECT name from ir_module_module WHERE state IN ('installed', 'to upgrade')")
    return [name for (name,) in cr.fetchall()]


def _get_test_files_by_module(modules):
    assert isinstance(modules, list), 'modules argument should be a list'
    test_files = {}
    for module in modules:
        info = load_information_from_description_file(module)
        test_files[module] = info.get('test', [])
    return test_files


def _run_test(cr, module, filename):
    _, ext = os.path.splitext(filename)
    pathname = os.path.join(module, filename)
    with tools.file_open(pathname) as fp:
        if ext == '.sql':
            if hasattr(tools, 'convert_sql_import'):
                tools.convert_sql_import(cr, fp)
            else:
                queries = fp.read().split(';')
                for query in queries:
                    new_query = ' '.join(query.split())
                    if new_query:
                        cr.execute(new_query)
        elif ext == '.csv':
            tools.convert_csv_import(cr, module, pathname, fp.read(), idref=None, mode='update', noupdate=False)
        elif ext == '.yml':
            if release.major_version >= '7.0':
                tools.convert_yaml_import(cr, module, fp, kind='test', idref=None, mode='update', noupdate=False)
            else:
                tools.convert_yaml_import(cr, module, fp, idref=None, mode='update', noupdate=False)
        elif ext == '.xml':
            tools.convert_xml_import(cr, module, fp, idref=None, mode='update', noupdate=False)


def _build_error_message():
    # Yaml traceback doesn't work, certainly because of the compile clause
    # that messes up line numbers
    error_msg = traceback.format_exc()
    frame_list = inspect.trace()
    deepest_frame = frame_list[-1][0]
    possible_yaml_statement = None
    for frame_inf in frame_list:
        frame = frame_inf[0]
        for local in ('statements', 'code_context', 'model'):
            if local not in frame.f_locals:
                break
        else:
            # all locals found ! we are in process_python function
            possible_yaml_statement = frame.f_locals['statements']
    if possible_yaml_statement:
        numbered_line_statement = ""
        for index, line in enumerate(possible_yaml_statement.split('\n'), start=1):
            numbered_line_statement += "%03d>  %s\n" % (index, line)
        yaml_error = "For yaml file, check the line number indicated in the traceback against this statement:\n%s"
        yaml_error = yaml_error % numbered_line_statement
        error_msg += '\n\n%s' % yaml_error
    error_msg += """\n\nLocal variables in deepest are: %s """ % repr(deepest_frame.f_locals)
    return error_msg


def _run_other_tests(dbname, modules, ignore):
    db = sql_db.db_connect(dbname)
    with closing(db.cursor()) as cr:
        test_files_by_module = _get_test_files_by_module(modules)
        for module in test_files_by_module:
            ignored_files = ignore.get(module, [])
            if ignored_files == 'all':
                ignored_files = test_files_by_module[module]
            for filename in test_files_by_module[module]:
                vals = {
                    'module': module,
                    'file': filename,
                }
                if filename in ignored_files:
                    vals['result'] = 'ignored'
                    _write_log(vals)
                    continue
                start = time.time()
                try:
                    _run_test(cr, module, filename)
                except Exception, e:
                    vals['duration'] = time.time() - start
                    vals['result'] = 'error'
                    vals['code'] = e.__class__.__name__
                    vals['exception'] = e.value if hasattr(e, 'value') else e.message
                    if filename.endswith('.yml'):
                        vals['exception'] += '\n\n%s' % _build_error_message()
                    _write_log(vals)
                else:
                    vals['duration'] = time.time() - start
                    vals['result'] = 'success'
                    _write_log(vals)
            cr.rollback()


def _run_unit_tests(dbname, modules, ignore):
    if run_unit_tests:
        for module in modules:
            vals = {'module': module}
            if module in ignore:
                vals['result'] = 'ignored'
                _write_log(vals)
            start = time.time()
            try:
                run_unit_tests(module, dbname)
            except Exception, e:
                vals['duration'] = time.time() - start
                vals['result'] = 'error'
                vals['exception'] = repr(e)
                _write_log(vals)
            else:
                vals['duration'] = time.time() - start
                vals['result'] = 'success'
                _write_log(vals)


def run_tests(dbname):
    ignore = eval(tools.config.get('ignored_tests') or '{}')
    db = sql_db.db_connect(dbname)
    with closing(db.cursor()) as cr:
        modules = _get_modules_list(cr)
        _run_unit_tests(dbname, modules, ignore)
        _run_other_tests(dbname, modules, ignore)
    return True

native_dispatch = common.dispatch


def new_dispatch(*args):
    i = release.major_version < '8.0' and 1 or 0
    if args[i] == 'run_tests':
        params = args[i+1]
        admin_passwd = params[0]
        security.check_super(admin_passwd)
        params = params[1:]
        return run_tests(*params)
    return native_dispatch(*args)

common.dispatch = new_dispatch
