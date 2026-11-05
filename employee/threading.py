"""
Async email sending for employee-invitation actions.
"""

import logging
from threading import Thread

logger = logging.getLogger(__name__)


class InvitationMailSendThread(Thread):
    """
    Sends a "set your password" invitation email to each of the given
    employees in the background, so a bulk invite doesn't block the
    request for the time it takes to send N emails.
    """

    def __init__(self, request, employees):
        Thread.__init__(self)
        self.employees = employees
        # Captured eagerly off the request -- this thread outlives the
        # request/response cycle, so the request object itself can't be
        # held onto (matches leave/threading.py's LeaveMailSendThread).
        self.host = request.get_host()
        self.is_secure = request.is_secure()

    def run(self) -> None:
        super().run()
        # Local import -- employee.methods.methods imports employee.models,
        # and importing it at module load time here (alongside
        # employee/views.py also importing this module) risks a circular
        # import during Django's app-loading sequence.
        from employee.methods.methods import send_employee_invitation

        for employee in self.employees:
            send_employee_invitation(employee, self.host, self.is_secure)
