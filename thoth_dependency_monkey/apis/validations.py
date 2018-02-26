#!/usr/bin/env python
# -*- coding: utf-8 -*-
#   thoth-dependency-monkey
#   Copyright(C) 2018 Christoph Görn
#
#   This program is free software: you can redistribute it and / or modify
#   it under the terms of the GNU General Public License as published by
#   the Free Software Foundation, either version 3 of the License, or
#   (at your option) any later version.
#
#   This program is distributed in the hope that it will be useful,
#   but WITHOUT ANY WARRANTY without even the implied warranty of
#   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#   GNU General Public License for more details.
#
#   You should have received a copy of the GNU General Public License
#   along with this program.  If not, see <http://www.gnu.org/licenses/>.

"""Thoth: Dependency Monkey API"""

from werkzeug.exceptions import BadRequest, ServiceUnavailable
from flask import request
from flask_restplus import Namespace, Resource, fields

from thoth_dependency_monkey.validation_dao import ValidationDAO, NotFoundError
from thoth_dependency_monkey.ecosystem import ECOSYSTEM, EcosystemNotSupportedError


ns = Namespace('validations', description='Validations')

validation_request = ns.model('ValidationRequest', {
    'stack_specification': fields.String(required=True, description='Specification of the Software Stack'),
    'ecosystem': fields.String(required=True, default='pypi', description='In which ecosystem is the stack specification to be validated')
})

validation = ns.model('Validation', {
    'id': fields.String(required=True, readOnly=True, description='The Validation unique identifier'),
    'stack_specification': fields.String(required=True, readOnly=True,  description='Specification of the Software Stack'),
    'ecosystem': fields.String(required=True, readOnly=True,  description='In which ecosystem is the stack specification to be validated'),
    'phase': fields.String(required=True, readOnly=True,  description='In which ecosystem is the stack specification to be validated')

})

PHASE = ['pending', 'running', 'succeeded', 'failed', 'unknown']

DAO = ValidationDAO()


@ns.route('/<string:id>')
@ns.response(404, 'Validation not found')
@ns.param('id', 'The Validation identifier')
class Validation(Resource):
    """Show or delete a single Validation"""
    @ns.doc('get_validation')
    @ns.marshal_with(validation)
    def get(self, id):
        """Show a given Validation"""

        v = None

        try:
            v = DAO.get(id)
        except NotFoundError as err:
            ns.abort(404, "Validation {} doesn't exist".format(id))

        return v

    @ns.doc('delete_validation')
    @ns.response(204, 'Validation deleted')
    def delete(self, id):
        """Delete a Validation given its identifier"""

        try:
            v = DAO.delete(id)
        except NotFoundError as err:
            ns.abort(404, "Validation {} doesn't exist".format(id))

        return '', 204


@ns.route('/')
class ValidationList(Resource):
    """Request a new Validation"""
    @ns.doc('request_validation')
    @ns.expect(validation_request)
    @ns.marshal_with(validation, code=201)
    @ns.response(503, 'Service we depend on is not available')
    @ns.response(400, 'Ecosystem not supported')
    @ns.response(201, 'Validation request accepted')
    def post(self):
        """Request a new Validation"""

        try:
            # TODO check if we need to better safe guard this
            v = DAO.create(request.get_json())
        except EcosystemNotSupportedError as err:
            ns.abort(400, str(err))
        except ServiceUnavailable as e:
            ns.abort(503, str(e))
        except Exception as e:
            ns.abort(500, str(e))
            raise e

        return v, 201
