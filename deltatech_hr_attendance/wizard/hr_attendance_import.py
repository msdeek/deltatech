# -*- coding: utf-8 -*-
# ©  2017 Deltatech
# See README.rst file on addons root folder for license details

import base64
from io import StringIO
import os

from odoo import models, fields, api, _, registry
from odoo.exceptions import  Warning, RedirectWarning,  ValidationError, UserError
import odoo.addons.decimal_precision as dp
from dateutil.relativedelta import relativedelta
import csv
import sys
import pytz
from odoo.tools import DEFAULT_SERVER_DATETIME_FORMAT, float_compare, float_round
import threading

"""
Index,Event Type,Card Holder,Card Type,Card No.,Event Time,Direction,Event Source,MAC Address,Card Swiping Type,Card Reader Type,Remark
1,Fingerprint/Finger Vein Authentication Passed,Cristi,,'0000000001,2018-05-23 16:12:29,Exit,HiK:Usa2 - Iesire,,,,
2,Fingerprint/Finger Vein Authentication Passed,Cristi,,'0000000001,2018-05-23 16:12:26,Enter,HiK:Usa2 - Intrare,,,,
3,Fingerprint/Finger Vein Authentication Passed,Cristi,,'0000000001,2018-05-23 16:12:23,Exit,HiK:Usa1 - Iesire,,,,
4,Fingerprint/Finger Vein Authentication Passed,Cristi,,'0000000001,2018-05-23 16:12:20,Enter,HiK:Usa1 - Intrare,,,,

"""
employees = {}


class hr_attendance_import(models.TransientModel):
    _name = 'hr.attendance.import'
    _description = "HR Attendance Import"

    background = fields.Boolean('Run in background', default=False)

    state = fields.Selection([('choose', 'choose'),
                              ('result', 'result')], default='choose')  # get the file

    attendance_file = fields.Binary(string='Attendance File')
    attendance_file_name = fields.Char(string='Attendance File Name')

    def add_attendance(self, event_time, barcode, direction):
        employee_data = employees.get(barcode, False)

        if not employee_data:
            employee = self.env['hr.employee'].search([('barcode', '=', barcode)])
            if not employee:
                employee = self.env['hr.employee'].with_context(active_test=False).search([('barcode', '=', barcode)])
            last_attendance = False
        else:
            employee = employee_data['employee']
            last_attendance =  employee_data['last_attendance']

        tz_name = self._context.get('tz') or self.env.user.tz or "Europe/Bucharest"
        local = pytz.timezone(tz_name)
        local_dt = local.localize(fields.Datetime.from_string(event_time), is_dst=None)
        event_time = fields.Datetime.to_string(local_dt.astimezone(pytz.utc))

        attendance = self.env['hr.attendance']
        values = {
            'employee_id': employee.id,
            'check_in': event_time
        }

        is_ok = True

        if not last_attendance or direction == 'sign_in':
            last_attendance = self.env['hr.attendance'].search([
                ('employee_id', '=', employee.id),
                ('check_in', '<=', event_time),
            ], order='check_in desc', limit=1)

        if direction == 'sign_in':
            if last_attendance.check_in == event_time:
                is_ok = False
            else:
                no_check_out_attendance = self.env['hr.attendance'].search([
                    ('employee_id', '=', employee.id),
                    ('check_out', '=', False),
                ], limit=1)
                if no_check_out_attendance:
                    if no_check_out_attendance.check_in == event_time:
                        is_ok = False  # exista deja inregistrarea
                    else:
                        if last_attendance != no_check_out_attendance:
                            if event_time < no_check_out_attendance.check_in:
                                no_check_out_attendance.unlink()  # inregistrarea va fi regenerata
                            else:
                                try:
                                    event_time_out = fields.Datetime.from_string(no_check_out_attendance.check_in)
                                    event_time_out = event_time_out +  relativedelta(seconds =1)
                                    event_time_out = fields.Datetime.to_string(event_time_out)
                                    no_check_out_attendance.write({'check_out': event_time_out,  'no_check_out': True})
                                except ValidationError as e:
                                    print(str(e), 'event_time', event_time)

            if last_attendance and (not last_attendance.check_out or last_attendance.check_out > event_time):
                is_ok = False
        else:
            if not last_attendance or last_attendance.check_in > event_time:
                is_ok = False

        if is_ok:
            if direction == 'sign_in':
                try:
                    last_attendance = self.env['hr.attendance'].create(values)
                except ValidationError as e:
                    print(str(e), values)

            else:
                try:
                    last_attendance.write({'check_out': event_time})
                    if self.background:
                        self._cr.commit()
                except ValidationError as e:
                    print(str(e), )

        employees[barcode] = { 'employee': employee,
                                   'last_attendance': last_attendance}
        return last_attendance

    @api.multi
    def do_import(self):
        if self.background:
            threaded_import = threading.Thread(target=self._do_import_background, args=())
            threaded_import.start()
            return {'type': 'ir.actions.act_window_close'}
        else:
            return self._do_import()

    @api.multi
    def _do_import_background(self):

        with api.Environment.manage():
            new_cr = registry(self._cr.dbname).cursor()
            job = self.with_env(self.env(cr=new_cr))
            job._do_import()
            new_cr.commit()
            new_cr.close()

    @api.multi
    def _do_import(self):
        attendance_file = base64.decodestring(self.attendance_file)
        buff = StringIO(attendance_file)

        reader = csv.DictReader(buff, quoting=csv.QUOTE_NONE, delimiter=',')
        attendances = self.env['hr.attendance']
        sortedlist = sorted(reader, key=lambda row: row['Event Time'])
        for row in sortedlist:
            if row['Direction'] == 'Enter':
                direction = 'sign_in'
            elif row['Direction'] == 'Exit':
                direction = 'sign_out'
            else:
                direction = 'action'
            attendance = self.add_attendance(event_time=row['Event Time'], barcode=row['Card No.'], direction=direction)
            if attendance:
                attendances |= attendance

        return {
            'domain': "[('id','in', [" + ','.join(map(str, attendances.ids)) + "])]",
            'name': _('Imported Attendances'),
            'view_type': 'form',
            'view_mode': 'tree,form',
            'res_model': 'hr.attendance',
            'view_id': False,
            'type': 'ir.actions.act_window',

        }
